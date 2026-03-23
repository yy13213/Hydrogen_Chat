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


def _add_project(user_id: int, project_dir: str, question: str):
    data = _load_history(user_id)
    data[project_dir] = {
        "question": question,
        "created_at": datetime.now().isoformat(),
        "status": "running",
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


# ── 代理请求工具 ──────────────────────────────────────────────

def _proxy_get(path: str, params: dict = None):
    """转发 GET 请求到 deep_research 服务"""
    try:
        resp = requests.get(
            f"{_DR_BASE}{path}",
            params=params,
            timeout=_DR_TIMEOUT,
        )
        return resp
    except requests.exceptions.ConnectionError:
        return None
    except Exception:
        return None


def _proxy_post(path: str, payload: dict):
    """转发 POST 请求到 deep_research 服务"""
    try:
        resp = requests.post(
            f"{_DR_BASE}{path}",
            json=payload,
            timeout=_DR_TIMEOUT,
        )
        return resp
    except requests.exceptions.ConnectionError:
        return None
    except Exception:
        return None


# ── API 路由 ──────────────────────────────────────────────────

@research_api_bp.post("/start")
@login_required
def start_research():
    """启动深度研究，返回 project_dir（时间戳）"""
    body = request.get_json(silent=True) or {}
    question = (body.get("question") or "").strip()
    if not question:
        return jsonify({"error": "研究问题不能为空"}), 400

    resp = _proxy_post("/research", {"question": question})
    if resp is None:
        return jsonify({"error": "无法连接到 Deep Research 服务，请确认 deep_research/main.py 已启动（端口 3031）"}), 503

    if resp.status_code != 200:
        try:
            detail = resp.json().get("detail", resp.text)
        except Exception:
            detail = resp.text
        return jsonify({"error": f"Deep Research 服务错误：{detail}"}), resp.status_code

    data = resp.json()
    project_dir = data.get("project_dir", "")
    if project_dir:
        _add_project(current_user.id, project_dir, question)

    return jsonify(data)


@research_api_bp.get("/projects")
@login_required
def list_projects():
    """列出当前用户的历史研究项目"""
    projects = _get_user_projects(current_user.id)
    return jsonify({"projects": projects})


@research_api_bp.get("/progress/<project_dir>")
@login_required
def get_progress(project_dir: str):
    """轮询研究进展（代理转发 + 同步用户历史状态）"""
    resp = _proxy_get(f"/progress/{project_dir}")
    if resp is None:
        return jsonify({"error": "无法连接到 Deep Research 服务"}), 503
    if resp.status_code == 404:
        return jsonify({"error": "项目不存在"}), 404
    if resp.status_code != 200:
        return jsonify({"error": "获取进展失败"}), resp.status_code

    data = resp.json()
    # 同步用户历史状态
    status = data.get("status", "")
    if status in ("completed", "failed"):
        _update_project_status(current_user.id, project_dir, status)

    return jsonify(data)


@research_api_bp.get("/detail/<project_dir>")
@login_required
def get_detail(project_dir: str):
    """获取详细数据（思维导图、质疑等）"""
    resp = _proxy_get(f"/detail/{project_dir}")
    if resp is None:
        return jsonify({"error": "无法连接到 Deep Research 服务"}), 503
    if resp.status_code != 200:
        return jsonify({"error": "获取详情失败"}), resp.status_code
    return jsonify(resp.json())


@research_api_bp.get("/report/<project_dir>")
@login_required
def get_report(project_dir: str):
    """获取最终研究报告（Markdown 原始内容）"""
    resp = _proxy_get(f"/report/{project_dir}")
    if resp is None:
        return jsonify({"error": "无法连接到 Deep Research 服务"}), 503
    if resp.status_code == 404:
        return jsonify({"error": "报告尚未生成"}), 404
    if resp.status_code != 200:
        return jsonify({"error": "获取报告失败"}), resp.status_code
    # 返回原始 Markdown 文本
    return current_app.response_class(
        resp.text,
        mimetype="text/plain; charset=utf-8",
    )


@research_api_bp.get("/logs/<project_dir>")
@login_required
def get_logs(project_dir: str):
    """获取项目日志"""
    log_type = request.args.get("log_type", "research")
    lines = request.args.get("lines", 200)
    resp = _proxy_get(f"/logs/{project_dir}", params={"log_type": log_type, "lines": lines})
    if resp is None:
        return jsonify({"error": "无法连接到 Deep Research 服务"}), 503
    return current_app.response_class(
        resp.text,
        mimetype="text/plain; charset=utf-8",
    )
