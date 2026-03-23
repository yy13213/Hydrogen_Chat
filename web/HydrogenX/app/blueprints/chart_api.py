"""
Hydrogen Chart API Blueprint
代理转发 chart_agent/main.py 的 FastAPI 服务（端口 9621）。
维护用户-项目历史 JSON 表：instance/chart_histories/user_{id}.json
图片通过 Cloudinary 图床中转，前端直接使用 CDN URL 渲染。
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
_CA_TIMEOUT = int(os.getenv("CHART_AGENT_TIMEOUT", "180"))   # AI 生成可能较慢
_CHART_REQUEST_TIMEOUT_SECONDS = int(os.getenv("HYDROGEN_CHART_REQUEST_TIMEOUT_SECONDS", "240"))

# ── Cloudinary 配置 ───────────────────────────────────────────
_CLOUDINARY_URL = os.getenv(
    "CLOUDINARY_URL",
    "cloudinary://197649926776445:StS2x9wYGP3wkyNT_XFuIRPqyvM@dmxrefnzd"
)

def _parse_cloudinary_url(url: str):
    """解析 cloudinary://api_key:api_secret@cloud_name"""
    m = re.match(r"cloudinary://([^:]+):([^@]+)@(.+)", url or "")
    if not m:
        return None, None, None
    return m.group(1), m.group(2), m.group(3)

_CL_API_KEY, _CL_API_SECRET, _CL_CLOUD_NAME = _parse_cloudinary_url(_CLOUDINARY_URL)

# ── 异步任务表（内存） ─────────────────────────────────────────
_JOBS = {}
_JOBS_LOCK = threading.Lock()
_EXECUTOR = ThreadPoolExecutor(max_workers=4)


def _upload_to_cloudinary(image_url_on_agent: str) -> str:
    """
    将 chart_agent 返回的相对图片 URL 下载后上传到 Cloudinary，
    返回 Cloudinary CDN URL。失败时返回原始 URL。
    """
    if not _CL_CLOUD_NAME:
        return image_url_on_agent

    # 拼接 chart_agent 的完整图片地址
    full_url = _CA_BASE.rstrip("/") + "/" + image_url_on_agent.lstrip("/")
    try:
        img_resp = requests.get(full_url, timeout=30)
        if not img_resp.ok:
            return image_url_on_agent
        img_bytes = img_resp.content
    except Exception:
        return image_url_on_agent

    # 上传到 Cloudinary（使用 Basic Auth）
    upload_url = f"https://api.cloudinary.com/v1_1/{_CL_CLOUD_NAME}/image/upload"
    try:
        import base64 as _b64
        resp = requests.post(
            upload_url,
            auth=(_CL_API_KEY, _CL_API_SECRET),
            files={"file": ("chart.png", img_bytes, "image/png")},
            data={"upload_preset": "ml_default"} if False else {},
            timeout=30,
        )
        if resp.ok:
            return resp.json().get("secure_url", image_url_on_agent)
    except Exception:
        pass

    # 降级：直接返回 chart_agent 的完整 URL
    return full_url


def _build_agent_image_url(project_name: str, image_ref: str) -> str:
    """
    将 chart_agent 的图片引用（可能是绝对路径/相对路径/文件名）转换为可访问 URL。
    """
    if not image_ref:
        return image_ref
    ref = str(image_ref).strip()
    if ref.startswith("http://") or ref.startswith("https://"):
        return ref

    # 已经是 /projects/... 形式
    if ref.startswith("/projects/"):
        return _CA_BASE.rstrip("/") + ref

    # 绝对路径里包含 /projects/<project_name>/...
    marker = f"/projects/{project_name}/"
    if marker in ref:
        tail = ref.split(marker, 1)[1].replace("\\", "/")
        return f"{_CA_BASE.rstrip('/')}/projects/{project_name}/{tail}"

    # 最后降级：当成项目目录下文件名
    name = Path(ref).name
    return f"{_CA_BASE.rstrip('/')}/projects/{project_name}/{name}"


def _normalize_progress_records(project_name: str, records: list) -> list:
    """
    统一进度记录中的图片字段，确保前端拿到的是可访问 URL（优先 Cloudinary）。
    """
    normalized = []
    for r in records or []:
        item = dict(r)
        src_files = item.get("files") or item.get("image_paths") or []
        # 每句对话只保留最后一张图片，避免同一气泡渲染多图
        if isinstance(src_files, list) and src_files:
            src_files = [src_files[-1]]
        render_files = []
        for f in src_files:
            agent_url = _build_agent_image_url(project_name, f)
            # 再上传/转换到 Cloudinary，保证外网可访问
            cdn_url = _upload_to_cloudinary(agent_url)
            render_files.append(cdn_url)
        if render_files:
            item["render_files"] = render_files
        normalized.append(item)
    return normalized


def _extract_status_chain(records: list) -> list:
    """从 jsonl 记录提取去重状态链（排除 user_turn/model_turn）。"""
    statuses = []
    for r in records or []:
        s = (r.get("status") or "").strip()
        if not s or s in ("user_turn", "model_turn"):
            continue
        if not statuses or statuses[-1] != s:
            statuses.append(s)
    return statuses


def _is_project_finished(records: list) -> bool:
    """判断项目本轮是否已到最终输出（出现 model_turn）。"""
    for r in records or []:
        if r.get("status") == "model_turn":
            return True
    return False


# ── 用户历史记录文件 ──────────────────────────────────────────

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


# ── 代理请求工具 ──────────────────────────────────────────────

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
    """统一处理 chart_agent 返回，补齐用户历史与 Cloudinary 图片。"""
    data = dict(resp_json or {})
    returned_project = data.get("project_name", "")

    # 维护用户历史
    if returned_project:
        existing = _load_history(user_id)
        if returned_project not in existing:
            _add_project(user_id, returned_project, user_input)
        else:
            _touch_project(user_id, returned_project)

    # 将图片上传到 Cloudinary，替换为 CDN URL
    raw_image_urls = data.get("image_urls") or []
    # 每句对话只保留最后一张图片
    if isinstance(raw_image_urls, list) and raw_image_urls:
        raw_image_urls = [raw_image_urls[-1]]
    cdn_image_urls = []
    for img_url in raw_image_urls:
        cdn_url = _upload_to_cloudinary(img_url)
        cdn_image_urls.append(cdn_url)
    data["image_urls"] = cdn_image_urls
    return data


def _run_chart_job(job_id: str, user_id: int, user_input: str, project_name: str, forwarded_files: list):
    """后台执行 chart_agent 请求，完成后写入任务表。"""
    form_data = {"user_input": user_input}
    if project_name:
        form_data["project_name"] = project_name

    with _JOBS_LOCK:
        _JOBS[job_id] = {
            "status": "running",
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


# ── API 路由 ──────────────────────────────────────────────────

@chart_api_bp.route("/projects", methods=["GET"])
@login_required
def list_projects():
    """列出当前用户的历史图表项目"""
    projects = _get_user_projects(current_user.id)
    return jsonify({"projects": projects})


@chart_api_bp.route("/chat", methods=["POST"])
@login_required
def chat():
    """
    发送消息（支持文件上传）。
    首次调用不传 project_name，服务端创建新项目并返回。
    后续多轮对话传入 project_name。
    """
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

    data = resp.json()
    returned_project = data.get("project_name", "")

    data = _process_chat_response(current_user.id, user_input, data)
    return jsonify(data)


@chart_api_bp.route("/chat_async", methods=["POST"])
@login_required
def chat_async():
    """
    异步提交图表任务，立即返回 job_id，前端轮询 /api/chart/jobs/<job_id> 获取结果。
    避免外网网关对长请求返回 502。
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
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if not job:
            return jsonify({"error": "任务不存在或已过期"}), 404
        status = job.get("status")
        payload = {
            "job_id": job_id,
            "status": status,
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
    """轮询执行记录（jsonl 条目列表）"""
    resp = _proxy_get(f"/progress/{project_name}")
    if resp is None:
        return jsonify({"error": "无法连接到 Chart Agent 服务"}), 503
    if resp.status_code == 404:
        return jsonify({"error": "项目不存在"}), 404
    if resp.status_code != 200:
        # 将上游错误细节透传，便于前端定位问题
        try:
            detail = resp.json().get("detail", resp.text[:300])
        except Exception:
            detail = resp.text[:300]
        return jsonify({"error": f"获取进展失败：{detail}"}), resp.status_code

    data = resp.json()
    data["records"] = _normalize_progress_records(project_name, data.get("records") or [])
    return jsonify(data)


@chart_api_bp.route("/status/<project_name>", methods=["GET"])
@login_required
def get_status(project_name):
    """
    轻量状态接口：仅返回状态链 + 完成标识，供前端高频轮询实时渲染。
    """
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
        return jsonify({"error": f"获取状态失败：{detail}"}), resp.status_code

    data = resp.json()
    records = data.get("records") or []
    statuses = _extract_status_chain(records)
    finished = _is_project_finished(records)
    return jsonify({
        "project_name": project_name,
        "statuses": statuses,
        "finished": finished,
    })


@chart_api_bp.route("/projects/<project_name>", methods=["DELETE"])
@login_required
def delete_project(project_name):
    """从用户历史中删除项目记录"""
    _delete_project_history(current_user.id, project_name)
    return jsonify({"ok": True})
