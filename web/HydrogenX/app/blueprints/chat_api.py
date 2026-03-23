"""
Hydrogen Chat API Blueprint
逻辑完全基于 chat/app.py + chat/history.py + chat/tools.py
通过 Flask API 暴露给前端，支持：
  - 会话管理（创建/列表/删除/获取消息）按用户隔离
  - 发送消息（支持文件上传、工具调用、自动生成标题）
"""
import json
import os
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from datetime import datetime
from pathlib import Path
from typing import Optional

from flask import Blueprint, current_app, jsonify, request
from flask_login import current_user, login_required

# ── 将 chat/ 目录加入 sys.path，复用工具模块 ────────────────
def _find_chat_dir() -> Path:
    # 兼容 Windows/Linux 的目录层级差异：向上查找仓库根下的 chat/
    for p in Path(__file__).resolve().parents:
        cand = p / "chat"
        if (cand / "tools.py").exists() and (cand / "history.py").exists():
            return cand
    raise FileNotFoundError("无法定位 chat/ 目录（需要 chat/tools.py 与 chat/history.py）")


_CHAT_DIR = _find_chat_dir()
if str(_CHAT_DIR) not in sys.path:
    sys.path.insert(0, str(_CHAT_DIR))

from dotenv import load_dotenv
load_dotenv(_CHAT_DIR / ".env")

from google import genai
from google.genai import types
from tools import search_web, query_knowledge_base, query_database

# 标题生成复用 chat/history.py 的 generate_title
from history import generate_title as _generate_title_from_msgs

chat_api_bp = Blueprint("chat_api", __name__, url_prefix="/api/chat")

# ── 环境变量 ────────────────────────────────────────────────
GEMINI_BASE_URL = os.getenv("GEMINI_BASE_URL", "http://localhost:6773")
GEMINI_API_KEY  = os.getenv("GEMINI_API_KEY", "placeholder")
CHAT_MODEL      = os.getenv("CHAT_MODEL", "gemini-3.1-pro-preview-customtools")
CHAT_REQUEST_TIMEOUT_SECONDS = int(os.getenv("HYDROGEN_CHAT_REQUEST_TIMEOUT_SECONDS", "180"))

# ── 每用户历史文件路径 ────────────────────────────────────────
def _history_file(user_id: int) -> Path:
    """每个用户独立的聊天历史 JSON 文件"""
    base = Path(__file__).parent.parent.parent / "instance" / "chat_histories"
    base.mkdir(parents=True, exist_ok=True)
    return base / f"user_{user_id}.json"


# ── 历史记录操作（按用户隔离） ────────────────────────────────
def _load(user_id: int) -> dict:
    f = _history_file(user_id)
    if not f.exists():
        return {}
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(user_id: int, data: dict):
    _history_file(user_id).write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _get_all_sessions(user_id: int) -> list:
    data = _load(user_id)
    sessions = []
    for sid, s in data.items():
        sessions.append({
            "id": sid,
            "title": s.get("title", "新对话"),
            "created_at": s.get("created_at", ""),
            "updated_at": s.get("updated_at", ""),
            "messages": s.get("messages", []),
        })
    sessions.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
    return sessions


def _create_session(user_id: int) -> str:
    data = _load(user_id)
    sid = str(uuid.uuid4())[:8]
    now = datetime.now().isoformat()
    data[sid] = {"title": "新对话", "created_at": now, "updated_at": now, "messages": []}
    _save(user_id, data)
    return sid


def _get_session_messages(user_id: int, session_id: str) -> list:
    return _load(user_id).get(session_id, {}).get("messages", [])


def _add_message(user_id: int, session_id: str, role: str, content: str, extra: Optional[dict] = None):
    data = _load(user_id)
    if session_id not in data:
        return
    msg = {"role": role, "content": content, "timestamp": datetime.now().isoformat()}
    if extra:
        msg.update(extra)
    data[session_id]["messages"].append(msg)
    data[session_id]["updated_at"] = datetime.now().isoformat()
    _save(user_id, data)


def _update_session_title(user_id: int, session_id: str, title: str):
    data = _load(user_id)
    if session_id in data:
        data[session_id]["title"] = title
        _save(user_id, data)


def _delete_session(user_id: int, session_id: str):
    data = _load(user_id)
    if session_id in data:
        del data[session_id]
        _save(user_id, data)


def _delete_empty_sessions(user_id: int):
    """删除没有任何消息的空会话"""
    data = _load(user_id)
    empty = [sid for sid, s in data.items() if not s.get("messages")]
    if empty:
        for sid in empty:
            del data[sid]
        _save(user_id, data)


# ── Gemini 工具定义 ─────────────────────────────────────────
TOOLS = [
    types.Tool(
        function_declarations=[
            types.FunctionDeclaration(
                name="search_web",
                description="使用 Tavily 搜索网络，获取最新资讯、新闻或实时信息",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={"query": types.Schema(type=types.Type.STRING, description="搜索关键词或问题")},
                    required=["query"]
                )
            ),
            types.FunctionDeclaration(
                name="query_knowledge_base",
                description="查询本地知识库，获取专业领域文档和资料",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={"question": types.Schema(type=types.Type.STRING, description="要查询的问题")},
                    required=["question"]
                )
            ),
            types.FunctionDeclaration(
                name="query_database",
                description="查询数据库，自动生成 SQL 并返回结果。传入用户的自然语言问题即可，无需手写 SQL。",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={"user_question": types.Schema(type=types.Type.STRING, description="用自然语言描述要查询的内容")},
                    required=["user_question"]
                )
            )
        ]
    )
]

CUSTOM_TOOL_NAMES = {"search_web", "query_knowledge_base", "query_database"}


def _get_client():
    return genai.Client(
        api_key=GEMINI_API_KEY,
        http_options=types.HttpOptions(base_url=GEMINI_BASE_URL)
    )


def _file_to_part(file_storage) -> types.Part:
    file_bytes = file_storage.read()
    mime_type = file_storage.mimetype or "application/octet-stream"
    return types.Part.from_bytes(data=file_bytes, mime_type=mime_type)


def _build_contents(messages: list) -> list:
    contents = []
    for msg in messages:
        role = "user" if msg["role"] == "user" else "model"
        contents.append(types.Content(
            role=role,
            parts=[types.Part.from_text(text=msg["content"])]
        ))
    return contents


def _execute_tool(tool_name: str, tool_args: dict) -> str:
    if tool_name == "search_web":
        return search_web(tool_args.get("query", ""))
    elif tool_name == "query_knowledge_base":
        return query_knowledge_base(tool_args.get("question", ""))
    elif tool_name == "query_database":
        return query_database(tool_args.get("user_question", ""))
    return "未知工具"


def _build_error_response(err: str) -> str:
    if "111" in err or "Connection refused" in err:
        return (
            "❌ **无法连接到 Gemini 代理服务**\n\n"
            f"**错误详情：** `{err}`\n\n"
            "**可能原因：** Gemini 代理服务未启动，请先运行 `python api/Google_ai2dify_port6773.py`"
        )
    elif "timeout" in err.lower() or "timed out" in err.lower():
        return f"❌ **请求超时**\n\n**错误详情：** `{err}`\n\n请稍后重试。"
    elif "401" in err or "403" in err or "API key" in err.lower():
        return f"❌ **API 鉴权失败**\n\n**错误详情：** `{err}`\n\n请检查 API Key 配置。"
    elif "400" in err:
        return f"❌ **请求参数错误**\n\n**错误详情：** `{err}`"
    return f"❌ **请求失败**\n\n**错误详情：** `{err}`"


def _generate_response(user_message: str, history: list, file_parts: list) -> dict:
    """
    核心生成逻辑（同步）
    返回 {"response": str, "tool_calls": list[str]}
    """
    client = _get_client()

    user_parts = file_parts + [types.Part.from_text(text=user_message)]
    history_contents = _build_contents(history)
    all_contents = history_contents + [
        types.Content(role="user", parts=user_parts)
    ]

    used_tools: list = []
    TOOL_MAX_FAILURES = 3
    tool_failure_counts: dict = {}
    abandoned_tools: set = set()

    def _call_tool_with_limit(tool_name: str, tool_args: dict) -> str:
        if tool_name in abandoned_tools:
            return f"[工具 {tool_name} 已不可用，请根据已有信息直接回答]"
        try:
            result = _execute_tool(tool_name, tool_args)
            tool_failure_counts[tool_name] = 0
            return result
        except Exception as e:
            tool_failure_counts[tool_name] = tool_failure_counts.get(tool_name, 0) + 1
            count = tool_failure_counts[tool_name]
            if count >= TOOL_MAX_FAILURES:
                abandoned_tools.add(tool_name)
                return (
                    f"[工具 {tool_name} 连续失败 {TOOL_MAX_FAILURES} 次已放弃。"
                    f"最后错误：{e}。请根据已有信息直接回答用户问题。]"
                )
            return f"[工具 {tool_name} 调用失败（第 {count}/{TOOL_MAX_FAILURES} 次）：{e}]"

    def _all_tools_abandoned() -> bool:
        return CUSTOM_TOOL_NAMES <= abandoned_tools

    full_response = ""
    max_tool_rounds = 10

    for _ in range(max_tool_rounds):
        try:
            use_tools = not _all_tools_abandoned()
            config_kwargs = dict(
                temperature=0.7,
                system_instruction=(
                    "你是 Hydrogen Chat 智能助手，能够搜索网络、查询知识库和数据库来回答问题。"
                    "请用中文回复。回答时尽量结构清晰，使用 Markdown 格式。"
                )
            )
            if use_tools:
                config_kwargs["tools"] = TOOLS
                config_kwargs["tool_config"] = types.ToolConfig(
                    function_calling_config=types.FunctionCallingConfig(
                        mode=types.FunctionCallingConfigMode.AUTO
                    )
                )
            response = client.models.generate_content(
                model=CHAT_MODEL,
                contents=all_contents,
                config=types.GenerateContentConfig(**config_kwargs)
            )
        except Exception as e:
            full_response = _build_error_response(str(e))
            break

        custom_tool_calls = []
        if response.candidates and response.candidates[0].content.parts:
            for part in response.candidates[0].content.parts:
                if (hasattr(part, "function_call") and part.function_call
                        and part.function_call.name in CUSTOM_TOOL_NAMES):
                    custom_tool_calls.append(part.function_call)

        if custom_tool_calls:
            all_contents.append(response.candidates[0].content)
            function_response_parts = []
            for fc in custom_tool_calls:
                tool_name = fc.name
                tool_args = dict(fc.args) if fc.args else {}
                if tool_name not in used_tools:
                    used_tools.append(tool_name)
                tool_result = _call_tool_with_limit(tool_name, tool_args)
                function_response_parts.append(
                    types.Part.from_function_response(
                        name=tool_name,
                        response={"result": tool_result}
                    )
                )
            all_contents.append(types.Content(role="user", parts=function_response_parts))
        else:
            full_response = response.text or ""
            break

    return {"response": full_response, "tool_calls": used_tools}


# ── Routes ──────────────────────────────────────────────────

@chat_api_bp.get("/sessions")
@login_required
def list_sessions():
    """获取当前用户的所有会话（过滤空会话，按更新时间倒序）"""
    uid = current_user.id
    # 先清理空会话
    _delete_empty_sessions(uid)
    sessions = _get_all_sessions(uid)
    # 返回时去掉 messages 字段（只返回元数据）
    return jsonify([
        {"id": s["id"], "title": s["title"], "updated_at": s["updated_at"]}
        for s in sessions
    ])


@chat_api_bp.post("/sessions")
@login_required
def new_session():
    """创建新会话"""
    uid = current_user.id
    sid = _create_session(uid)
    return jsonify({"id": sid, "title": "新对话"}), 201


@chat_api_bp.delete("/sessions/<string:session_id>")
@login_required
def remove_session(session_id: str):
    """删除会话"""
    _delete_session(current_user.id, session_id)
    return jsonify({"message": "已删除"})


@chat_api_bp.get("/sessions/<string:session_id>/messages")
@login_required
def get_messages(session_id: str):
    """获取指定会话的消息列表"""
    uid = current_user.id
    msgs = _get_session_messages(uid, session_id)
    sessions = _get_all_sessions(uid)
    title = next((s["title"] for s in sessions if s["id"] == session_id), "对话")
    return jsonify({"messages": msgs, "title": title})


@chat_api_bp.post("/send")
@login_required
def send_message():
    """
    发送消息并获取 AI 回复
    multipart/form-data:
      - message: str
      - session_id: str
      - files: (optional) multiple files
    """
    uid = current_user.id
    message    = (request.form.get("message") or "").strip()
    session_id = (request.form.get("session_id") or "").strip()

    if not message:
        return jsonify({"error": "消息不能为空"}), 400
    if not session_id:
        return jsonify({"error": "session_id 不能为空"}), 400

    # 处理上传文件
    uploaded_files = request.files.getlist("files")
    file_parts = []
    file_names = []
    for uf in uploaded_files:
        if uf and uf.filename:
            try:
                part = _file_to_part(uf)
                file_parts.append(part)
                file_names.append(uf.filename)
            except Exception as e:
                current_app.logger.warning("文件处理失败 %s: %s", uf.filename, e)

    # 会话必须存在
    sessions = _get_all_sessions(uid)
    if not any(s["id"] == session_id for s in sessions):
        return jsonify({"error": "会话不存在或已被删除，请新建会话后重试。"}), 404

    # 获取历史消息（不含本次）
    history = _get_session_messages(uid, session_id)

    # 保存用户消息
    _add_message(uid, session_id, "user", message, {
        "files": file_names,
        "tool_calls": []
    })

    # 生成回复（增加超时保护，避免反向代理 502）
    executor = ThreadPoolExecutor(max_workers=1)
    try:
        future = executor.submit(_generate_response, message, history, file_parts)
        result = future.result(timeout=CHAT_REQUEST_TIMEOUT_SECONDS)
    except TimeoutError:
        current_app.logger.warning(
            "Hydrogen Chat timeout: session=%s user=%s timeout=%ss",
            session_id,
            uid,
            CHAT_REQUEST_TIMEOUT_SECONDS,
        )
        executor.shutdown(wait=False, cancel_futures=True)
        return jsonify(
            {
                "error": (
                    f"请求处理超时（>{CHAT_REQUEST_TIMEOUT_SECONDS}s）。"
                    "请精简问题或稍后重试。"
                )
            }
        ), 504
    except Exception as e:
        current_app.logger.exception("生成回复失败")
        executor.shutdown(wait=False, cancel_futures=True)
        return jsonify({"error": f"生成回复失败: {str(e)}"}), 500
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    full_response = result["response"]
    used_tools    = result["tool_calls"]

    # 保存助手回复
    _add_message(uid, session_id, "assistant", full_response, {
        "tool_calls": used_tools
    })

    # 异步生成标题（首次对话后，标题仍为"新对话"时触发）
    all_msgs = _get_session_messages(uid, session_id)
    sessions = _get_all_sessions(uid)
    current_title = next((s["title"] for s in sessions if s["id"] == session_id), "新对话")
    new_title = None

    if len(all_msgs) >= 2 and current_title == "新对话":
        msgs_snapshot = all_msgs[:4]
        sid_snapshot  = session_id

        def gen_title():
            t = _generate_title_from_msgs(msgs_snapshot)
            _update_session_title(uid, sid_snapshot, t)

        threading.Thread(target=gen_title, daemon=True).start()
        # 前端下次 loadSessions 时会拿到新标题
    else:
        new_title = current_title if current_title != "新对话" else None

    return jsonify({
        "response":   full_response,
        "tool_calls": used_tools,
        "title":      new_title,
    })
