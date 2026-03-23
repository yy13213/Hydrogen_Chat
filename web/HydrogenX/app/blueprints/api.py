import re
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request, send_file
from flask_login import current_user, login_required
from sqlalchemy import func

from ..extensions import db
from ..models import ChatTask, GeneratedArtifact, TaskUploadAttachment, UploadedFile
from ..services.artifact_service import ArtifactService
from ..services.task_queue import task_queue
from ..services.upload_service import UploadService, UploadValidationError

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

    if not isinstance(upload_ids, list):
        return jsonify({"error": "upload_ids 必须为数组。"}), 400

    normalized_upload_ids = []
    seen_upload_ids = set()
    for raw_id in upload_ids:
        try:
            upload_id = int(raw_id)
        except (TypeError, ValueError):
            return jsonify({"error": "upload_ids 中存在非法值。"}), 400
        if upload_id in seen_upload_ids:
            continue
        seen_upload_ids.add(upload_id)
        normalized_upload_ids.append(upload_id)

    max_files_per_task = int(current_app.config["HYDROGEN_MAX_FILES_PER_TASK"])
    if len(normalized_upload_ids) > max_files_per_task:
        return jsonify({"error": f"单次任务最多可附带 {max_files_per_task} 个文件。"}), 400

    uploads = []
    if normalized_upload_ids:
        uploads = (
            UploadedFile.query.filter(
                UploadedFile.user_id == current_user.id,
                UploadedFile.id.in_(normalized_upload_ids),
            )
            .order_by(UploadedFile.created_at.desc())
            .all()
        )
        if len(uploads) != len(normalized_upload_ids):
            return jsonify({"error": "存在无效文件，或文件不属于当前用户。"}), 400

        invalid_uploads = [upload.filename for upload in uploads if upload.extraction_status != "ready" or not upload.extracted_text]
        if invalid_uploads:
            return jsonify({"error": f"以下文件尚不可用于提问：{', '.join(invalid_uploads)}"}), 400

    task = ChatTask(
        user_id=current_user.id,
        agent_id=agent_id,
        session_key=build_session_key(current_user),
        prompt=prompt,
        status="queued",
    )
    db.session.add(task)
    db.session.flush()

    for upload in uploads:
        db.session.add(TaskUploadAttachment(task_id=task.id, upload_id=upload.id))

    db.session.commit()
    task_queue.enqueue(task.id)

    return jsonify({"message": "任务已入队。", "task": task.to_dict()}), 202


@api_bp.get("/tasks/<int:task_id>")
@login_required
def get_task(task_id: int):
    task = ChatTask.query.filter_by(id=task_id, user_id=current_user.id).first_or_404()
    return jsonify(task.to_dict())


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


@api_bp.get("/uploads")
@login_required
def list_uploads():
    uploads = (
        UploadedFile.query.filter_by(user_id=current_user.id)
        .order_by(UploadedFile.created_at.desc())
        .limit(100)
        .all()
    )
    return jsonify([upload.to_dict() for upload in uploads])


@api_bp.post("/uploads")
@login_required
def upload_files():
    files = request.files.getlist("files")
    if not files:
        return jsonify({"error": "请先选择至少一个文件。"}), 400

    max_batch_files = int(current_app.config["HYDROGEN_MAX_FILES_PER_TASK"])
    if len(files) > max_batch_files:
        return jsonify({"error": f"单次最多上传 {max_batch_files} 个文件。"}), 400

    upload_service = UploadService()
    saved_uploads = []
    try:
        for file_storage in files:
            saved_uploads.append(upload_service.save_upload(file_storage, current_user.id))
        for upload in saved_uploads:
            db.session.add(upload)
        db.session.commit()
    except UploadValidationError as exc:
        db.session.rollback()
        for upload in saved_uploads:
            try:
                upload_service.delete_upload_file(upload)
            except Exception:
                current_app.logger.warning("Failed to cleanup upload file after validation error: %s", upload.filename)
        return jsonify({"error": str(exc)}), 400
    except Exception:
        db.session.rollback()
        for upload in saved_uploads:
            try:
                upload_service.delete_upload_file(upload)
            except Exception:
                current_app.logger.warning("Failed to cleanup upload file after unexpected error: %s", upload.filename)
        current_app.logger.exception("Failed to save uploads for user=%s", current_user.id)
        return jsonify({"error": "文件上传失败，请稍后重试。"}), 500

    return jsonify({"message": "文件上传成功。", "uploads": [upload.to_dict() for upload in saved_uploads]}), 201


@api_bp.get("/uploads/<int:upload_id>/download")
@login_required
def download_upload(upload_id: int):
    upload = UploadedFile.query.filter_by(id=upload_id, user_id=current_user.id).first_or_404()
    file_path = UploadService().upload_abspath(upload)
    if not file_path.exists() or not file_path.is_file():
        return jsonify({"error": "原始上传文件不存在或已被清理。"}), 404

    return send_file(
        file_path,
        mimetype=upload.mime_type or "application/octet-stream",
        as_attachment=True,
        download_name=upload.filename,
        conditional=True,
        etag=True,
        max_age=0,
    )


@api_bp.delete("/uploads/<int:upload_id>")
@login_required
def delete_upload(upload_id: int):
    upload = UploadedFile.query.filter_by(id=upload_id, user_id=current_user.id).first_or_404()
    linked_task_count = db.session.query(func.count(TaskUploadAttachment.id)).filter_by(upload_id=upload.id).scalar() or 0

    if linked_task_count:
        return jsonify({"error": "该文件已被历史任务引用，不能删除。"}), 400

    try:
        UploadService().delete_upload_file(upload)
        db.session.delete(upload)
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Failed to delete upload=%s user=%s", upload_id, current_user.id)
        return jsonify({"error": "删除文件失败，请稍后重试。"}), 500

    return jsonify({"message": "文件已删除。"})


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
