import re

from flask import Blueprint, current_app, jsonify, request, send_file
from flask_login import current_user, login_required
from sqlalchemy import func

from ..extensions import db
from ..models import ChatTask, GeneratedArtifact, UploadedFile
from ..services.artifact_service import ArtifactService
from ..services.task_queue import task_queue
from ..services.upload_service import UploadService

api_bp = Blueprint("api", __name__, url_prefix="/api")


def build_user_label(user) -> str:
    raw_value = (getattr(user, "username", None) or getattr(user, "email", None) or str(getattr(user, "id", "user"))).strip()
    label = re.sub(r"[^a-zA-Z0-9._-]+", "-", raw_value).strip("-._")
    return (label or str(getattr(user, "id", "user")))[:64]


def build_session_key(user) -> str:
    return f"web:user:{build_user_label(user)}"


@api_bp.get("/tasks")
@login_required
def list_tasks():
    tasks = (
        ChatTask.query.filter_by(user_id=current_user.id)
        .order_by(ChatTask.created_at.desc())
        .limit(20)
        .all()
    )
    return jsonify([task.to_dict() for task in tasks])


@api_bp.post("/tasks")
@login_required
def create_task():
    payload = request.get_json(silent=True) or {}
    prompt = (payload.get("prompt") or "").strip()
    agent_id = (payload.get("agent_id") or current_app.config["OPENCLAW_DEFAULT_AGENT"]).strip()
    upload_ids = payload.get("upload_ids") or []

    if not prompt:
        return jsonify({"error": "prompt 不能为空。"}), 400

    if len(prompt) > 8000:
        return jsonify({"error": "prompt 长度不能超过 8000 字符。"}), 400

    if agent_id not in current_app.config["OPENCLAW_ALLOWED_AGENTS"]:
        return jsonify({"error": "非法 agent_id。"}), 400

    uploads = []
    if upload_ids:
        upload_ids = [int(x) for x in upload_ids]
        uploads = (
            UploadedFile.query.filter(
                UploadedFile.user_id == current_user.id,
                UploadedFile.id.in_(upload_ids),
            )
            .order_by(UploadedFile.created_at.desc())
            .all()
        )
        if len(uploads) != len(set(upload_ids)):
            return jsonify({"error": "存在无效的上传文件。"}), 400

    task = ChatTask(
        user_id=current_user.id,
        agent_id=agent_id,
        session_key=build_session_key(current_user),
        prompt=prompt,
        status="queued",
    )
    if uploads:
        task.uploads.extend(uploads)

    db.session.add(task)
    db.session.commit()

    task_queue.enqueue(task.id)

    return jsonify({"message": "任务已入队。", "task": task.to_dict()}), 202


@api_bp.get("/tasks/<int:task_id>")
@login_required
def get_task(task_id: int):
    task = ChatTask.query.filter_by(id=task_id, user_id=current_user.id).first_or_404()
    return jsonify(task.to_dict())


@api_bp.post("/uploads")
@login_required
def upload_file():
    if "file" not in request.files:
        return jsonify({"error": "未找到上传文件字段。"}), 400

    file = request.files["file"]
    if not file or not file.filename:
        return jsonify({"error": "请选择文件。"}), 400

    try:
        upload = UploadService().save_upload(file=file, user_id=current_user.id)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    db.session.add(upload)
    db.session.commit()
    return jsonify({"message": "上传成功。", "upload": upload.to_dict()}), 201


@api_bp.get("/uploads")
@login_required
def list_uploads():
    uploads = (
        UploadedFile.query.filter_by(user_id=current_user.id)
        .order_by(UploadedFile.created_at.desc())
        .limit(50)
        .all()
    )
    return jsonify([upload.to_dict() for upload in uploads])


@api_bp.get("/uploads/<int:upload_id>/download")
@login_required
def download_upload(upload_id: int):
    upload = UploadedFile.query.filter_by(id=upload_id, user_id=current_user.id).first_or_404()
    file_path = UploadService().upload_abspath(upload)

    if not file_path.exists() or not file_path.is_file():
        return jsonify({"error": "上传文件不存在或已被清理。"}), 404

    return send_file(
        file_path,
        mimetype=upload.mime_type or "application/octet-stream",
        as_attachment=True,
        download_name=upload.filename,
        conditional=True,
        etag=True,
        max_age=0,
    )


@api_bp.get("/artifacts")
@login_required
def list_artifacts():
    artifacts = (
        GeneratedArtifact.query.filter_by(user_id=current_user.id)
        .order_by(GeneratedArtifact.created_at.desc())
        .limit(100)
        .all()
    )
    return jsonify([artifact.to_dict() for artifact in artifacts])


@api_bp.get("/artifacts/<int:artifact_id>/download")
@login_required
def download_artifact(artifact_id: int):
    artifact = GeneratedArtifact.query.filter_by(id=artifact_id, user_id=current_user.id).first_or_404()
    file_path = ArtifactService().artifact_abspath(artifact)

    if not file_path.exists() or not file_path.is_file():
        return jsonify({"error": "文件不存在或已被清理。"}), 404

    return send_file(
        file_path,
        mimetype=artifact.mime_type or "application/octet-stream",
        as_attachment=True,
        download_name=artifact.filename,
        conditional=True,
        etag=True,
        max_age=0,
    )


@api_bp.get("/system/queue")
@login_required
def queue_status():
    running_total = db.session.query(func.count(ChatTask.id)).filter(ChatTask.status == "running").scalar()
    queued_total = db.session.query(func.count(ChatTask.id)).filter(ChatTask.status == "queued").scalar()
    return jsonify(
        {
            "queued_total": queued_total,
            "running_total": running_total,
            "max_concurrent": current_app.config["OPENCLAW_MAX_CONCURRENT"],
            "allowed_agents": current_app.config["OPENCLAW_ALLOWED_AGENTS"],
            "default_agent": current_app.config["OPENCLAW_DEFAULT_AGENT"],
        }
    )
