import json
import os
import uuid
from datetime import datetime
from typing import Optional
from google import genai
from google.genai import types

HISTORY_FILE = os.path.join(os.path.dirname(__file__), "chat_history.json")
GEMINI_BASE_URL = "http://localhost:6773"


def _load_all() -> dict:
    if not os.path.exists(HISTORY_FILE):
        return {}
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_all(data: dict):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_all_sessions() -> list[dict]:
    """获取所有会话列表，按时间倒序"""
    data = _load_all()
    sessions = []
    for sid, session in data.items():
        sessions.append({
            "id": sid,
            "title": session.get("title", "新对话"),
            "created_at": session.get("created_at", ""),
            "updated_at": session.get("updated_at", ""),
            "messages": session.get("messages", [])
        })
    sessions.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
    return sessions


def create_session() -> str:
    """创建新会话，返回会话ID"""
    data = _load_all()
    sid = str(uuid.uuid4())[:8]
    now = datetime.now().isoformat()
    data[sid] = {
        "title": "新对话",
        "created_at": now,
        "updated_at": now,
        "messages": []
    }
    _save_all(data)
    return sid


def get_session_messages(session_id: str) -> list[dict]:
    """获取指定会话的消息列表"""
    data = _load_all()
    session = data.get(session_id, {})
    return session.get("messages", [])


def add_message(session_id: str, role: str, content: str, extra: Optional[dict] = None):
    """向会话添加消息"""
    data = _load_all()
    if session_id not in data:
        return
    msg = {
        "role": role,
        "content": content,
        "timestamp": datetime.now().isoformat()
    }
    if extra:
        msg.update(extra)
    data[session_id]["messages"].append(msg)
    data[session_id]["updated_at"] = datetime.now().isoformat()
    _save_all(data)


def update_session_title(session_id: str, title: str):
    """更新会话标题"""
    data = _load_all()
    if session_id in data:
        data[session_id]["title"] = title
        _save_all(data)


def delete_session(session_id: str):
    """删除会话"""
    data = _load_all()
    if session_id in data:
        del data[session_id]
        _save_all(data)


def generate_title(messages: list[dict]) -> str:
    """使用 gemini-3.1-flash-lite-preview 为对话生成标题"""
    if not messages:
        return "新对话"
    try:
        client = genai.Client(
            api_key="placeholder",
            http_options=types.HttpOptions(base_url=GEMINI_BASE_URL)
        )
        conversation_text = "\n".join(
            f"{m['role']}: {m['content'][:200]}"
            for m in messages[:4]
        )
        prompt = f"请为以下对话生成一个简洁的标题（不超过15个字，不加引号）：\n\n{conversation_text}"
        response = client.models.generate_content(
            model="gemini-3.1-flash-lite-preview",
            contents=prompt
        )
        title = response.text.strip().strip('"').strip("'")
        return title[:30] if title else "新对话"
    except Exception:
        if messages:
            first_user = next((m["content"] for m in messages if m["role"] == "user"), "新对话")
            return first_user[:20]
        return "新对话"
