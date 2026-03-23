"""
Hydrogen Chart API Blueprint
代理转发 chart_agent/main.py 的 FastAPI 服务（端口 9621）。
维护用户-项目历史 JSON 表：instance/chart_histories/user_{id}.json
图片通过 Cloudinary 图床中转，前端直接使用 CDN URL 渲染。
CDN URL 持久化回写到 chart_agent 的 session.jsonl，保证历史记录可用。
"""
import json
import os
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

import requests
from flask import Blueprint, jsonify, request, current_app
from flask_login import current_user, login_required

chart_api_bp = Blueprint("chart_api", __name__, url_prefix="/api/chart")

# ── chart_agent 服务地址 ──────────────────────────────────────
_CA_BASE    = os.getenv("CHART_AGENT_BASE_URL", "http://localhost:9621")
_CA_TIMEOUT = int(os.getenv("CHART_AGENT_TIMEOUT", "180"))
_CHART_REQUEST_TIMEOUT_SECONDS = int(os.getenv("HYDROGEN_CHART_REQUEST_TIMEOUT_SECONDS", "240"))

# ── Cloudinary 配置 ───────────────────────────────────────────
_CLOUDINARY_URL = os.getenv(
    "CLOUDINARY_URL",
    "cloudinary://197649926776445:StS2x9wYGP3wkyNT_XFuIRPqyvM@dmxrefnzd"
)

def _parse_cloudinary_url(url: str):
    m = re.match(r"cloudinary://([^:]+):([^@]+)@(.+)", url or "")
    if not m:
        return None, None, None
    return m.group(1), m.group(2), m.group(3)

_CL_API_KEY, _CL_API_SECRET, _CL_CLOUD_NAME = _parse_cloudinary_url(_CLOUDINARY_URL)

# ── 异步任务表（内存） ─────────────────────────────────────────
_JOBS = {}
_JOBS_LOCK = threading.Lock()
_EXECUTOR = ThreadPoolExecutor(max_workers=4)

# ── chart_agent projects 目录（用于回写 jsonl） ───────────────
def _find_chart_agent_projects_dir() -> Path:
    """定位 chart_agent/projects 目录（相对于本文件向上查找）"""
    for p in Path(__file__).resolve().parents:
        cand = p / "chart_agent" / "projects"
        if cand.exists():
            return cand
    return None

_CHART_PROJECTS_DIR = _find_chart_agent_projects_dir()


# ═══════════════════════════════════════════════════════════
# Cloudinary 图片上传
# ═══════════════════════════════════════════════════════════

def _build_agent_image_url(project_name: str, image_ref: str) -> str:
    """将 chart_agent 的图片引用转换为可访问 URL"""
    if not image_ref:
        return image_ref
    ref = str(image_ref).strip()
    if ref.startswith("http://") or ref.startswith("https://"):
        return ref
    if ref.startswith("/projects/"):
        return _CA_BASE.rstrip("/") + ref
    marker = f"/projects/{project_name}/"
    if marker in ref:
        tail = ref.split(marker, 1)[1].replace("\\", "/")
        return f"{_CA_BASE.rstrip('/')}/projects/{project_name}/{tail}"
    name = Path(ref).name
    return f"{_CA_BASE.rstrip('/')}/projects/{project_name}/{name}"


def _upload_to_cloudinary(image_url_on_agent: str) -> str:
    """
    下载图片并上传到 Cloudinary，返回 CDN URL。
    失败时返回原始完整 URL（确保外网可访问）。
    """
    if not image_url_on_agent:
        return image_url_on_agent

    # 已经是 CDN URL
    if "cloudinary.com" in image_url_on_agent or "res.cloudinary.com" in image_url_on_agent:
        return image_url_on_agent

    # 确保是完整 URL
    if image_url_on_agent.startswith("/"):
        full_url = _CA_BASE.rstrip("/") + image_url_on_agent
    elif not image_url_on_agent.startswith("http"):
        full_url = _CA_BASE.rstrip("/") + "/" + image_url_on_agent.lstrip("/")
    else:
        full_url = image_url_on_agent

    if not _CL_CLOUD_NAME:
        return full_url

    try:
        img_resp = requests.get(full_url, timeout=30)
        if not img_resp.ok:
            return full_url
        img_bytes = img_resp.content
    except Exception:
        return full_url

    upload_url = f"https://api.cloudinary.com/v1_1/{_CL_CLOUD_NAME}/image/upload"
    try:
        resp = requests.post(
            upload_url,
            auth=(_CL_API_KEY, _CL_API_SECRET),
            files={"file": ("chart.png", img_bytes, "image/png")},
            timeout=30,
        )
        if resp.ok:
            cdn = resp.json().get("secure_url", "")
            if cdn:
                return cdn
    except Exception:
        pass

    return full_url


def _convert_image_refs_to_cdn(project_name: str, image_refs: list) -> list:
    """批量将图片引用转为 CDN URL"""
    result = []
    for ref in (image_refs or []):
        agent_url = _build_agent_image_url(project_name, ref)
        cdn_url = _upload_to_cloudinary(agent_url)
        result.append(cdn_url)
    return result


# ═══════════════════════════════════════════════════════════
# jsonl 回写（持久化 CDN URL）
# ═══════════════════════════════════════════════════════════

def _rewrite_jsonl_cdn_urls(project_name: str):
    """
    扫描 session.jsonl，将 model_turn / 图表生成完成 记录中的本地路径
    替换为 Cloudinary CDN URL，并回写文件。
    只处理尚未替换过的条目（不含 cloudinary.com 的路径）。
    """
    if not _CHART_PROJECTS_DIR:
        return
    jsonl_path = _CHART_PROJECTS_DIR / project_name / "session.jsonl"
    if not jsonl_path.exists():
        return

    lines = jsonl_path.read_text(encoding="utf-8").splitlines()
    changed = False
    new_lines = []

    for line in lines:
        line = line.strip()
        if not line:
            new_lines.append(line)
            continue
        try:
            record = json.loads(line)
        except Exception:
            new_lines.append(line)
            continue

        status = record.get("status", "")

        # model_turn: files 字段
        if status == "model_turn":
            files = record.get("files") or []
            needs_update = any(
                f and "cloudinary.com" not in str(f)
                for f in files
            )
            if needs_update:
                cdn_files = _convert_image_refs_to_cdn(project_name, files)
                # 只保留最后一张（用户要求每轮只展示最后一张）
                record["files"] = cdn_files
                record["render_file"] = cdn_files[-1] if cdn_files else ""
                changed = True

        # 图表生成完成: image_paths 字段
        if status == "图表生成完成":
            image_paths = record.get("image_paths") or []
            needs_update = any(
                p and "cloudinary.com" not in str(p)
                for p in image_paths
            )
            if needs_update:
                cdn_paths = _convert_image_refs_to_cdn(project_name, image_paths)
                record["image_paths"] = cdn_paths
                changed = True

        new_lines.append(json.dumps(record, ensure_ascii=False))

    if changed:
        jsonl_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


# ═══════════════════════════════════════════════════════════
# 进度记录规范化（供 /progress 接口使用）
# ═══════════════════════════════════════════════════════════

def _normalize_progress_records(project_name: str, records: list) -> list:
    """
    规范化进度记录：
    - model_turn: 只取最后一张图片，优先使用已持久化的 render_file
    - 其他状态: 透传
    """
    normalized = []
    for r in (records or []):
        item = dict(r)
        status = item.get("status", "")

        if status == "model_turn":
            # 优先用已回写的 render_file（CDN URL）
            render_file = item.get("render_file", "")
            if render_file and "cloudinary.com" in render_file:
                item["render_files"] = [render_file]
            else:
                # 回退：取 files 最后一个并转 CDN
                files = item.get("files") or []
                if files:
                    last = files[-1]
                    agent_url = _build_agent_image_url(project_name, last)
                    cdn_url = _upload_to_cloudinary(agent_url)
                    item["render_files"] = [cdn_url]
                else:
                    item["render_files"] = []

        normalized.append(item)
    return normalized


# ═══════════════════════════════════════════════════════════
# 用户历史记录
# ═══════════════════════════════════════════════════════════

def _history_file(user_id: int) -> Path:
    base = Path(__file__).parent.parent.parent / "instance" / "chart_histories"
    base.mkdir(parents=True, exist_ok=True)
    return base / f"user_{user_id}.json"


def _load_history(user_id: int) -> dict:
    f = _history_file(user_id)
    if not f.exists():
        return {}
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_history(user_id: int, data: dict):
    _history_file(user_id).write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _add_project(user_id: int, project_name: str, first_question: str):
    data = _load_history(user_id)
    data[project_name] = {
        "question": first_question,
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
    }
    _save_history(user_id, data)


def _touch_project(user_id: int, project_name: str):
    data = _load_history(user_id)
    if project_name in data:
        data[project_name]["updated_at"] = datetime.now().isoformat()
        _save_history(user_id, data)


def _get_user_projects(user_id: int) -> list:
    data = _load_history(user_id)
    projects = []
    for pn, info in data.items():
        projects.append({
            "project_name": pn,
            "question": info.get("question", ""),
            "created_at": info.get("created_at", ""),
            "updated_at": info.get("updated_at", ""),
        })
    projects.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
    return projects


def _delete_project_history(user_id: int, project_name: str):
    data = _load_history(user_id)
    if project_name in data:
        del data[project_name]
        _save_history(user_id, data)


# ═══════════════════════════════════════════════════════════
# 代理请求工具
# ═══════════════════════════════════════════════════════════

def _proxy_get(path: str, params: dict = None):
    try:
        resp = requests.get(
            f"{_CA_BASE}{path}", params=params, timeout=_CA_TIMEOUT
        )
        return resp
    except Exception:
        return None


def _proxy_post_form(path: str, data: dict, files=None):
    try:
        resp = requests.post(
            f"{_CA_BASE}{path}", data=data, files=files, timeout=_CA_TIMEOUT
        )
        return resp
    except Exception:
        return None


def _prepare_forwarded_files():
    """从当前请求读取上传文件并转为 requests 可转发格式（bytes）。"""
    forwarded_files = []
    for key in request.files:
        for fobj in request.files.getlist(key):
            if fobj and fobj.filename:
                forwarded_files.append(
                    ("files", (fobj.filename, fobj.read(), fobj.mimetype or "application/octet-stream"))
                )
    return forwarded_files


def _process_chat_response(user_id: int, user_input: str, resp_json: dict) -> dict:
    """统一处理 chart_agent 返回：维护用户历史、转换图片为 CDN URL、回写 jsonl。"""
    data = dict(resp_json or {})
    returned_project = data.get("project_name", "")

    # 维护用户历史
    if returned_project:
        existing = _load_history(user_id)
        if returned_project not in existing:
            _add_project(user_id, returned_project, user_input)
        else:
            _touch_project(user_id, returned_project)

    # 将图片上传到 Cloudinary，只保留最后一张
    raw_image_urls = data.get("image_urls") or []
    cdn_image_urls = []
    for img_url in raw_image_urls:
        cdn_url = _upload_to_cloudinary(img_url)
        cdn_image_urls.append(cdn_url)

    # 只保留最后一张
    data["image_urls"] = [cdn_image_urls[-1]] if cdn_image_urls else []

    # 回写 jsonl，持久化 CDN URL（异步，不阻塞响应）
    if returned_project:
        threading.Thread(
            target=_rewrite_jsonl_cdn_urls,
            args=(returned_project,),
            daemon=True,
        ).start()

    return data


# ═══════════════════════════════════════════════════════════
# 异步任务执行
# ═══════════════════════════════════════════════════════════

def _run_chart_job(job_id: str, user_id: int, user_input: str, project_name: str, forwarded_files: list):
    """后台执行 chart_agent 请求，完成后写入任务表。"""
    form_data = {"user_input": user_input}
    if project_name:
        form_data["project_name"] = project_name

    with _JOBS_LOCK:
        _JOBS[job_id] = {
            "status": "running",
            "project_name": project_name,
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "error": None,
            "data": None,
        }

    try:
        future = _EXECUTOR.submit(_proxy_post_form, "/chat", form_data, forwarded_files if forwarded_files else None)
        resp = future.result(timeout=_CHART_REQUEST_TIMEOUT_SECONDS)
        if resp is None:
            raise RuntimeError("无法连接到 Chart Agent 服务")
        if resp.status_code != 200:
            try:
                detail = resp.json().get("detail", resp.text[:400])
            except Exception:
                detail = resp.text[:400]
            raise RuntimeError(f"Chart Agent 服务错误：{detail}")

        payload = _process_chat_response(user_id, user_input, resp.json())
        with _JOBS_LOCK:
            _JOBS[job_id]["status"] = "completed"
            _JOBS[job_id]["updated_at"] = datetime.now().isoformat()
            _JOBS[job_id]["project_name"] = payload.get("project_name", project_name)
            _JOBS[job_id]["data"] = payload
    except FuturesTimeoutError:
        with _JOBS_LOCK:
            _JOBS[job_id]["status"] = "failed"
            _JOBS[job_id]["updated_at"] = datetime.now().isoformat()
            _JOBS[job_id]["error"] = f"处理超时（>{_CHART_REQUEST_TIMEOUT_SECONDS}s）"
    except Exception as e:
        with _JOBS_LOCK:
            _JOBS[job_id]["status"] = "failed"
            _JOBS[job_id]["updated_at"] = datetime.now().isoformat()
            _JOBS[job_id]["error"] = str(e)


# ═══════════════════════════════════════════════════════════
# API 路由
# ═══════════════════════════════════════════════════════════

@chart_api_bp.route("/projects", methods=["GET"])
@login_required
def list_projects():
    """列出当前用户的历史图表项目"""
    projects = _get_user_projects(current_user.id)
    return jsonify({"projects": projects})


@chart_api_bp.route("/chat", methods=["POST"])
@login_required
def chat():
    """同步发送消息（兼容旧前端）"""
    user_input   = (request.form.get("user_input") or "").strip()
    project_name = (request.form.get("project_name") or "").strip() or None

    if not user_input:
        return jsonify({"error": "消息不能为空"}), 400

    forwarded_files = _prepare_forwarded_files()
    form_data = {"user_input": user_input}
    if project_name:
        form_data["project_name"] = project_name

    resp = _proxy_post_form("/chat", data=form_data,
                            files=forwarded_files if forwarded_files else None)

    if resp is None:
        return jsonify({"error": "无法连接到 Chart Agent 服务，请确认 chart_agent/main.py 已启动（端口 9621）"}), 503

    if resp.status_code != 200:
        try:
            detail = resp.json().get("detail", resp.text[:400])
        except Exception:
            detail = resp.text[:400]
        return jsonify({"error": f"Chart Agent 服务错误：{detail}"}), resp.status_code

    data = _process_chat_response(current_user.id, user_input, resp.json())
    return jsonify(data)


@chart_api_bp.route("/chat_async", methods=["POST"])
@login_required
def chat_async():
    """
    异步提交图表任务，立即返回 job_id 和 project_name（已知时）。
    前端轮询 /api/chart/jobs/<job_id> 获取结果。
    """
    user_input = (request.form.get("user_input") or "").strip()
    project_name = (request.form.get("project_name") or "").strip() or None
    if not user_input:
        return jsonify({"error": "消息不能为空"}), 400

    forwarded_files = _prepare_forwarded_files()
    job_id = uuid.uuid4().hex[:16]
    threading.Thread(
        target=_run_chart_job,
        args=(job_id, int(current_user.id), user_input, project_name, forwarded_files),
        daemon=True,
    ).start()
    return jsonify({"job_id": job_id, "project_name": project_name})


@chart_api_bp.route("/jobs/<job_id>", methods=["GET"])
@login_required
def get_job(job_id):
    """轮询任务状态"""
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if not job:
            return jsonify({"error": "任务不存在或已过期"}), 404
        status = job.get("status")
        payload = {
            "job_id": job_id,
            "status": status,
            "project_name": job.get("project_name"),
            "created_at": job.get("created_at"),
            "updated_at": job.get("updated_at"),
            "error": job.get("error"),
        }
        if status == "completed":
            payload["data"] = job.get("data")
        return jsonify(payload)


@chart_api_bp.route("/progress/<project_name>", methods=["GET"])
@login_required
def get_progress(project_name):
    """
    轮询执行记录（jsonl 条目列表）。
    直接读取本地 jsonl 文件（比代理到 chart_agent 更快更稳定）。
    """
    # 优先直接读本地 jsonl，避免 502
    if _CHART_PROJECTS_DIR:
        jsonl_path = _CHART_PROJECTS_DIR / project_name / "session.jsonl"
        if jsonl_path.exists():
            records = []
            for line in jsonl_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except Exception:
                        pass
            normalized = _normalize_progress_records(project_name, records)
            return jsonify({"project_name": project_name, "records": normalized})

    # 降级：代理到 chart_agent
    resp = _proxy_get(f"/progress/{project_name}")
    if resp is None:
        return jsonify({"error": "无法连接到 Chart Agent 服务"}), 503
    if resp.status_code == 404:
        return jsonify({"error": "项目不存在"}), 404
    if resp.status_code != 200:
        try:
            detail = resp.json().get("detail", resp.text[:300])
        except Exception:
            detail = resp.text[:300]
        return jsonify({"error": f"获取进展失败：{detail}"}), resp.status_code

    data = resp.json()
    data["records"] = _normalize_progress_records(project_name, data.get("records") or [])
    return jsonify(data)


@chart_api_bp.route("/projects/<project_name>", methods=["DELETE"])
@login_required
def delete_project(project_name):
    """从用户历史中删除项目记录"""
    _delete_project_history(current_user.id, project_name)
    return jsonify({"ok": True})
