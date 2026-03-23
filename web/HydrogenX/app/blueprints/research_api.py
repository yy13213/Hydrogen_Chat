"""
Hydrogen Research API Blueprint
代理转发 deep_research/main.py 的 FastAPI 服务，同时维护用户-项目历史 JSON 表。

用户历史表：instance/research_histories/user_{id}.json
结构：{ "project_dir": { "question": "...", "created_at": "...", "status": "running|completed|failed" }, ... }

所有 /api/research/* 路由均需登录。
"""
import json
import os
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import requests
from flask import Blueprint, jsonify, request, current_app
from flask_login import current_user, login_required

research_api_bp = Blueprint("research_api", __name__, url_prefix="/api/research")

# deep_research 服务地址（可通过环境变量覆盖）
_DR_BASE = os.getenv("DEEP_RESEARCH_BASE_URL", "http://localhost:3031")
_DR_TIMEOUT = int(os.getenv("DEEP_RESEARCH_TIMEOUT", "15"))


# ── 用户历史记录文件 ──────────────────────────────────────────

def _history_file(user_id: int) -> Path:
    base = Path(__file__).parent.parent.parent / "instance" / "research_histories"
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


def _add_project(user_id: int, project_dir: str, question: str, dr_base: str):
    data = _load_history(user_id)
    data[project_dir] = {
        "question": question,
        "created_at": datetime.now().isoformat(),
        "status": "running",
        "dr_base": dr_base,
    }
    _save_history(user_id, data)


def _update_project_status(user_id: int, project_dir: str, status: str):
    data = _load_history(user_id)
    if project_dir in data:
        data[project_dir]["status"] = status
        _save_history(user_id, data)


def _get_user_projects(user_id: int) -> list:
    data = _load_history(user_id)
    projects = []
    for pd, info in data.items():
        projects.append({
            "project_dir": pd,
            "question": info.get("question", ""),
            "created_at": info.get("created_at", ""),
            "status": info.get("status", "unknown"),
        })
    projects.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return projects


def _get_project_dr_base(user_id: int, project_dir: str):
    data = _load_history(user_id)
    return (data.get(project_dir) or {}).get("dr_base")


def _normalized_base(url: str) -> str:
    return (url or "").strip().rstrip("/")


def _candidate_bases() -> list:
    """按优先级返回可尝试的 deep_research 服务地址。"""
    raw = [
        _normalized_base(_DR_BASE),
        "http://127.0.0.1:3031",
        "http://localhost:3031",
    ]
    seen = set()
    out = []
    for u in raw:
        if not u or u in seen:
            continue
        seen.add(u)
        out.append(u)
    return out


def _looks_like_self_loop(resp) -> bool:
    """识别被错误转发到当前 Web 服务本身的场景。"""
    if resp is None:
        return False
    ct = (resp.headers.get("Content-Type") or "").lower()
    text = (resp.text or "")[:800].lower()
    if resp.status_code in (404, 405) and ("text/html" in ct or "<!doctype html" in text):
        # 当前站点模板关键字
        if "hydrogenx" in text or "app-navbar" in text or "site-shell" in text:
            return True
    return False


# ── 代理请求工具 ──────────────────────────────────────────────

def _proxy_get(path: str, params: dict = None, base_url: str = None):
    """转发 GET 请求到 deep_research 服务"""
    target = _normalized_base(base_url or _DR_BASE)
    try:
        resp = requests.get(
            f"{target}{path}",
            params=params,
            timeout=_DR_TIMEOUT,
        )
        return resp
    except requests.exceptions.ConnectionError:
        return None
    except Exception:
        return None


def _proxy_post(path: str, payload: dict, base_url: str = None):
    """转发 POST 请求到 deep_research 服务"""
    target = _normalized_base(base_url or _DR_BASE)
    try:
        resp = requests.post(
            f"{target}{path}",
            json=payload,
            timeout=_DR_TIMEOUT,
        )
        return resp
def _proxy_post_with_fallback(path: str, payload: dict):
    """
    启动阶段自动探测可用 deep_research 地址。
    返回 (resp, used_base, error_text)
    """
    last_error = None
    for base in _candidate_bases():
        resp = _proxy_post(path, payload, base_url=base)
        if resp is None:
            last_error = f"{base} 连接失败"
            continue
        if _looks_like_self_loop(resp):
            last_error = f"{base} 指向了当前 Web 服务（/research 返回 HTML），不是 deep_research:3031"
            continue
        if resp.status_code == 200:
            return resp, base, None
        try:
            detail = resp.json().get("detail", resp.text)
        except Exception:
            detail = resp.text
        last_error = f"{base} 返回 {resp.status_code}: {detail[:500]}"
    return None, None, last_error
    except requests.exceptions.ConnectionError:
        return None
    except Exception:
        return None


# ── API 路由 ──────────────────────────────────────────────────

@research_api_bp.route("/start", methods=["POST"])
@login_required
def start_research():
    """启动深度研究，返回 project_dir（时间戳）"""
    body = request.get_json(silent=True) or {}
    question = (body.get("question") or "").strip()
    if not question:
        return jsonify({"error": "研究问题不能为空"}), 400

    resp, used_base, err = _proxy_post_with_fallback("/research", {"question": question})
    if resp is None:
        return jsonify({
            "error": (
                "无法连接到 Deep Research 服务。"
                "请确认 deep_research/main.py 已在同机 3031 端口启动，"
                "或设置正确的 DEEP_RESEARCH_BASE_URL。"
                f" 详情：{err or '未知错误'}"
            )
        }), 503

    data = resp.json()
    project_dir = data.get("project_dir", "")
    if project_dir:
        _add_project(current_user.id, project_dir, question, used_base or _normalized_base(_DR_BASE))

    return jsonify(data)


@research_api_bp.route("/projects", methods=["GET"])
@login_required
def list_projects():
    """列出当前用户的历史研究项目"""
    projects = _get_user_projects(current_user.id)
    return jsonify({"projects": projects})


@research_api_bp.route("/progress/<project_dir>", methods=["GET"])
@login_required
def get_progress(project_dir):
    """轮询研究进展（代理转发 + 同步用户历史状态）"""
    dr_base = _get_project_dr_base(current_user.id, project_dir) or _normalized_base(_DR_BASE)
    resp = _proxy_get(f"/progress/{project_dir}", base_url=dr_base)
    if resp is None:
        return jsonify({"error": "无法连接到 Deep Research 服务"}), 503
    if resp.status_code == 404:
        return jsonify({"error": "项目不存在"}), 404
    if resp.status_code != 200:
        return jsonify({"error": "获取进展失败"}), resp.status_code

    data = resp.json()
    status = data.get("status", "")
    if status in ("completed", "failed"):
        _update_project_status(current_user.id, project_dir, status)

    return jsonify(data)


@research_api_bp.route("/detail/<project_dir>", methods=["GET"])
@login_required
def get_detail(project_dir):
    """获取详细数据（思维导图、质疑等）"""
    dr_base = _get_project_dr_base(current_user.id, project_dir) or _normalized_base(_DR_BASE)
    resp = _proxy_get(f"/detail/{project_dir}", base_url=dr_base)
    if resp is None:
        return jsonify({"error": "无法连接到 Deep Research 服务"}), 503
    if resp.status_code != 200:
        return jsonify({"error": "获取详情失败"}), resp.status_code
    return jsonify(resp.json())


@research_api_bp.route("/report/<project_dir>", methods=["GET"])
@login_required
def get_report(project_dir):
    """获取最终研究报告（Markdown 原始内容）"""
    dr_base = _get_project_dr_base(current_user.id, project_dir) or _normalized_base(_DR_BASE)
    resp = _proxy_get(f"/report/{project_dir}", base_url=dr_base)
    if resp is None:
        return jsonify({"error": "无法连接到 Deep Research 服务"}), 503
    if resp.status_code == 404:
        return jsonify({"error": "报告尚未生成"}), 404
    if resp.status_code != 200:
        return jsonify({"error": "获取报告失败"}), resp.status_code
    return current_app.response_class(
        resp.text,
        mimetype="text/plain; charset=utf-8",
    )


@research_api_bp.route("/logs/<project_dir>", methods=["GET"])
@login_required
def get_logs(project_dir):
    """获取项目日志"""
    log_type = request.args.get("log_type", "research")
    lines = request.args.get("lines", 200)
    dr_base = _get_project_dr_base(current_user.id, project_dir) or _normalized_base(_DR_BASE)
    resp = _proxy_get(f"/logs/{project_dir}", params={"log_type": log_type, "lines": lines}, base_url=dr_base)
    if resp is None:
        return jsonify({"error": "无法连接到 Deep Research 服务"}), 503
    return current_app.response_class(
        resp.text,
        mimetype="text/plain; charset=utf-8",
    )
