import streamlit as st
import base64
import mimetypes
import os
import threading
from google import genai
from google.genai import types
from history import (
    get_all_sessions, create_session, get_session_messages,
    add_message, update_session_title, delete_session, generate_title
)
from tools import search_web, query_knowledge_base, query_database

# ── 页面配置 ──────────────────────────────────────────────
st.set_page_config(
    page_title="Hydrogen Chat",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded"
)

GEMINI_BASE_URL = "http://localhost:6773"
CHAT_MODEL = "gemini-3.1-pro-preview-customtools"

# ── 自定义样式 ─────────────────────────────────────────────
st.markdown("""
<style>
/* 整体背景 */
[data-testid="stAppViewContainer"] {
    background: #0f1117;
}
[data-testid="stSidebar"] {
    background: #161b27;
    border-right: 1px solid #2d3748;
}
/* 侧边栏标题 */
.sidebar-title {
    font-size: 1.2rem;
    font-weight: 700;
    color: #e2e8f0;
    padding: 0.5rem 0 1rem 0;
    border-bottom: 1px solid #2d3748;
    margin-bottom: 1rem;
}
/* 历史会话按钮 */
.session-btn {
    width: 100%;
    text-align: left;
    background: transparent;
    border: none;
    color: #a0aec0;
    padding: 0.5rem 0.75rem;
    border-radius: 8px;
    cursor: pointer;
    font-size: 0.875rem;
    transition: all 0.2s;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}
.session-btn:hover { background: #2d3748; color: #e2e8f0; }
.session-btn.active { background: #2b4a7a; color: #90cdf4; }
/* 工具调用提示 */
.tool-call-badge {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    background: #1a2744;
    border: 1px solid #2b4a7a;
    border-radius: 20px;
    padding: 4px 12px;
    font-size: 0.78rem;
    color: #63b3ed;
    margin: 4px 0;
}
/* 消息容器 */
.chat-container {
    max-width: 800px;
    margin: 0 auto;
}
/* 文件预览 */
.file-preview {
    background: #1a2035;
    border: 1px solid #2d3748;
    border-radius: 8px;
    padding: 8px 12px;
    font-size: 0.82rem;
    color: #a0aec0;
    margin: 4px 0;
}
</style>
""", unsafe_allow_html=True)


# ── Session State 初始化 ───────────────────────────────────
def init_state():
    if "current_session_id" not in st.session_state:
        st.session_state.current_session_id = None
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "pending_files" not in st.session_state:
        st.session_state.pending_files = []
    if "title_generated" not in st.session_state:
        st.session_state.title_generated = set()


init_state()


# ── Gemini 客户端 ──────────────────────────────────────────
@st.cache_resource
def get_client():
    return genai.Client(
        api_key="placeholder",
        http_options=types.HttpOptions(base_url=GEMINI_BASE_URL)
    )


# ── 工具定义 ───────────────────────────────────────────────
TOOLS = [
    types.Tool(
        function_declarations=[
            types.FunctionDeclaration(
                name="search_web",
                description="使用 Tavily 搜索网络，获取最新资讯、新闻或实时信息",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "query": types.Schema(
                            type=types.Type.STRING,
                            description="搜索关键词或问题"
                        )
                    },
                    required=["query"]
                )
            ),
            types.FunctionDeclaration(
                name="query_knowledge_base",
                description="查询本地知识库，获取专业领域文档和资料",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "question": types.Schema(
                            type=types.Type.STRING,
                            description="要查询的问题"
                        )
                    },
                    required=["question"]
                )
            ),
            types.FunctionDeclaration(
                name="query_database",
                description="查询数据库，自动生成 SQL 并返回结果。传入用户的自然语言问题即可，无需手写 SQL。",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "user_question": types.Schema(
                            type=types.Type.STRING,
                            description="用自然语言描述要查询的内容，例如：查询最近10篇文章的标题和发布时间"
                        )
                    },
                    required=["user_question"]
                )
            )
        ]
    )
]

CUSTOM_TOOL_NAMES = {"search_web", "query_knowledge_base", "query_database"}


# ── 工具执行 ───────────────────────────────────────────────
def execute_tool(tool_name: str, tool_args: dict) -> str:
    if tool_name == "search_web":
        return search_web(tool_args.get("query", ""))
    elif tool_name == "query_knowledge_base":
        return query_knowledge_base(tool_args.get("question", ""))
    elif tool_name == "query_database":
        return query_database(tool_args.get("user_question", ""))
    return "未知工具"


# ── 文件转换为 Gemini Part ─────────────────────────────────
def file_to_part(uploaded_file) -> types.Part:
    file_bytes = uploaded_file.read()
    mime_type = uploaded_file.type or "application/octet-stream"
    b64 = base64.b64encode(file_bytes).decode()
    return types.Part.from_bytes(data=base64.b64decode(b64), mime_type=mime_type)


# ── 构建历史消息 ───────────────────────────────────────────
def build_contents(messages: list[dict]) -> list[types.Content]:
    contents = []
    for msg in messages:
        role = "user" if msg["role"] == "user" else "model"
        contents.append(types.Content(
            role=role,
            parts=[types.Part.from_text(text=msg["content"])]
        ))
    return contents


# ── 侧边栏：历史记录 ───────────────────────────────────────
def render_sidebar():
    with st.sidebar:
        st.markdown('<div class="sidebar-title">⚡ Hydrogen Chat</div>', unsafe_allow_html=True)

        if st.button("＋  新建对话", use_container_width=True, type="primary"):
            sid = create_session()
            st.session_state.current_session_id = sid
            st.session_state.messages = []
            st.rerun()

        st.markdown("---")
        st.markdown("**历史对话**")

        sessions = get_all_sessions()
        if not sessions:
            st.caption("暂无历史对话")
        else:
            for session in sessions:
                sid = session["id"]
                title = session["title"]
                is_active = sid == st.session_state.current_session_id

                col1, col2 = st.columns([5, 1])
                with col1:
                    btn_type = "primary" if is_active else "secondary"
                    label = f"{'▶ ' if is_active else ''}{title}"
                    if st.button(label, key=f"sess_{sid}", use_container_width=True, type=btn_type):
                        st.session_state.current_session_id = sid
                        st.session_state.messages = get_session_messages(sid)
                        st.rerun()
                with col2:
                    if st.button("🗑", key=f"del_{sid}", help="删除此对话"):
                        delete_session(sid)
                        if st.session_state.current_session_id == sid:
                            st.session_state.current_session_id = None
                            st.session_state.messages = []
                        st.rerun()


# ── 渲染消息 ───────────────────────────────────────────────
def render_messages():
    for msg in st.session_state.messages:
        role = msg["role"]
        content = msg["content"]
        tool_calls = msg.get("tool_calls", [])

        with st.chat_message(role):
            # 显示工具调用徽章
            if tool_calls:
                for tc in tool_calls:
                    icon_map = {
                        "search_web": "🔍",
                        "query_knowledge_base": "📚",
                        "query_database": "🗄️",
                    }
                    icon = icon_map.get(tc, "🔧")
                    st.markdown(
                        f'<div class="tool-call-badge">{icon} 调用工具: <b>{tc}</b></div>',
                        unsafe_allow_html=True
                    )
            # 显示文件附件信息
            if msg.get("files"):
                for fname in msg["files"]:
                    st.markdown(
                        f'<div class="file-preview">📎 {fname}</div>',
                        unsafe_allow_html=True
                    )
            st.markdown(content)


# ── 流式生成回复 ───────────────────────────────────────────
def stream_response(user_message: str, uploaded_files: list):
    client = get_client()
    session_id = st.session_state.current_session_id

    # 构建当前轮次的用户内容
    user_parts = []
    file_names = []

    for uf in uploaded_files:
        try:
            part = file_to_part(uf)
            user_parts.append(part)
            file_names.append(uf.name)
        except Exception as e:
            st.warning(f"文件 {uf.name} 处理失败: {e}")

    user_parts.append(types.Part.from_text(text=user_message))

    # 历史消息（不含当前轮次）
    history_contents = build_contents(st.session_state.messages)
    all_contents = history_contents + [
        types.Content(role="user", parts=user_parts)
    ]

    # 保存用户消息
    add_message(session_id, "user", user_message, {
        "files": file_names,
        "tool_calls": []
    })
    st.session_state.messages.append({
        "role": "user",
        "content": user_message,
        "files": file_names,
        "tool_calls": []
    })

    # 工具调用记录
    used_tools = []

    # 显示用户消息
    with st.chat_message("user"):
        if file_names:
            for fname in file_names:
                st.markdown(
                    f'<div class="file-preview">📎 {fname}</div>',
                    unsafe_allow_html=True
                )
        st.markdown(user_message)

    # 生成回复（支持多轮工具调用）
    with st.chat_message("assistant"):
        tool_placeholder = st.empty()
        response_placeholder = st.empty()
        full_response = ""

        icon_map = {
            "search_web": "🔍",
            "query_knowledge_base": "📚",
            "query_database": "🗄️",
        }

        # 每个工具的失败次数，超过阈值后放弃该工具
        TOOL_MAX_FAILURES = 3
        tool_failure_counts: dict[str, int] = {}
        # 已放弃的工具集合，放弃后将错误信息直接回传，不再重试
        abandoned_tools: set[str] = set()

        def _call_tool_with_limit(tool_name: str, tool_args: dict) -> str:
            """执行工具，失败计数超限后放弃并返回降级提示"""
            if tool_name in abandoned_tools:
                return f"[工具 {tool_name} 已不可用，请根据已有信息直接回答]"
            try:
                result = execute_tool(tool_name, tool_args)
                # 重置该工具的失败计数（成功一次即重置）
                tool_failure_counts[tool_name] = 0
                return result
            except Exception as e:
                tool_failure_counts[tool_name] = tool_failure_counts.get(tool_name, 0) + 1
                count = tool_failure_counts[tool_name]
                if count >= TOOL_MAX_FAILURES:
                    abandoned_tools.add(tool_name)
                    return (
                        f"[工具 {tool_name} 连续失败 {TOOL_MAX_FAILURES} 次已放弃。"
                        f"最后错误：{e}。请根据已有信息直接回答用户问题，无需再调用此工具。]"
                    )
                return f"[工具 {tool_name} 调用失败（第 {count}/{TOOL_MAX_FAILURES} 次）：{e}，请重试或换用其他方式]"

        def _build_error_response(err: str) -> str:
            if "111" in err or "Connection refused" in err:
                return (
                    "❌ **无法连接到 Gemini 代理服务**\n\n"
                    f"**错误详情：** `{err}`\n\n"
                    "**可能原因及解决方法：**\n"
                    f"- Gemini 代理服务未启动，请先运行：`python api/Google_ai2dify_port6773.py`\n"
                    f"- 代理服务监听端口不是 `6773`，请检查 `GEMINI_BASE_URL` 配置\n"
                    "- 防火墙或网络策略阻止了本地端口访问"
                )
            elif "timeout" in err.lower() or "timed out" in err.lower():
                return (
                    "❌ **请求超时**\n\n"
                    f"**错误详情：** `{err}`\n\n"
                    "**可能原因：** 网络延迟过高，或 Gemini API 响应缓慢，请稍后重试。"
                )
            elif "401" in err or "403" in err or "API key" in err.lower():
                return (
                    "❌ **API 鉴权失败**\n\n"
                    f"**错误详情：** `{err}`\n\n"
                    "**可能原因：** API Key 无效或未配置，请检查代理服务的 `GEMINI_API_KEY` 环境变量。"
                )
            elif "400" in err:
                return (
                    "❌ **请求参数错误**\n\n"
                    f"**错误详情：** `{err}`\n\n"
                    "**可能原因：** 发送的内容格式不被模型支持，请检查上传的文件类型或消息内容。"
                )
            return f"❌ **请求失败**\n\n**错误详情：** `{err}`"

        # 当所有工具均被放弃时，切换到无工具模式让模型直接回答
        def _all_tools_abandoned() -> bool:
            return CUSTOM_TOOL_NAMES <= abandoned_tools

        max_tool_rounds = 10
        for _ in range(max_tool_rounds):
            try:
                # 所有工具都放弃后，不再传入工具列表，让模型直接回答
                use_tools = not _all_tools_abandoned()
                config_kwargs = dict(
                    temperature=0.7,
                    system_instruction="你是 Hydrogen Chat 智能助手，能够搜索网络、查询知识库和数据库来回答问题。请用中文回复。"
                )
                if use_tools:
                    config_kwargs["tools"] = TOOLS
                    config_kwargs["automatic_function_calling"] = types.AutomaticFunctionCallingConfig(disable=True)
                response = client.models.generate_content(
                    model=CHAT_MODEL,
                    contents=all_contents,
                    config=types.GenerateContentConfig(**config_kwargs)
                )
            except Exception as e:
                full_response = _build_error_response(str(e))
                response_placeholder.markdown(full_response)
                break

            # 检查是否有需要手动处理的自定义工具调用
            custom_tool_calls = []
            if response.candidates and response.candidates[0].content.parts:
                for part in response.candidates[0].content.parts:
                    if (hasattr(part, "function_call") and part.function_call
                            and part.function_call.name in CUSTOM_TOOL_NAMES):
                        custom_tool_calls.append(part.function_call)

            if custom_tool_calls:
                # 将模型回复（含 function_call）加入上下文
                all_contents.append(response.candidates[0].content)

                # 逐个执行工具（含失败限制）并收集结果
                function_response_parts = []
                for fc in custom_tool_calls:
                    tool_name = fc.name
                    tool_args = dict(fc.args) if fc.args else {}
                    if tool_name not in used_tools:
                        used_tools.append(tool_name)

                    icon = icon_map.get(tool_name, "🔧")
                    if tool_name in abandoned_tools:
                        tool_placeholder.markdown(
                            f'<div class="tool-call-badge">⚠️ 工具已放弃: <b>{tool_name}</b></div>',
                            unsafe_allow_html=True
                        )
                    else:
                        tool_placeholder.markdown(
                            f'<div class="tool-call-badge">{icon} 正在调用: <b>{tool_name}</b>...</div>',
                            unsafe_allow_html=True
                        )

                    tool_result = _call_tool_with_limit(tool_name, tool_args)
                    function_response_parts.append(
                        types.Part.from_function_response(
                            name=tool_name,
                            response={"result": tool_result}
                        )
                    )

                # 将所有工具结果一次性回传
                all_contents.append(types.Content(
                    role="user",
                    parts=function_response_parts
                ))
            else:
                # 无工具调用，直接输出文本回复
                tool_placeholder.empty()
                full_response = response.text or ""

                # 流式显示效果
                displayed = ""
                for char in full_response:
                    displayed += char
                    response_placeholder.markdown(displayed + "▌")
                response_placeholder.markdown(full_response)
                break

        # 保存助手回复
        add_message(session_id, "assistant", full_response, {
            "tool_calls": used_tools
        })
        st.session_state.messages.append({
            "role": "assistant",
            "content": full_response,
            "tool_calls": used_tools
        })

    # 异步生成标题（首次对话后）
    if (session_id not in st.session_state.title_generated
            and len(st.session_state.messages) >= 2):
        st.session_state.title_generated.add(session_id)
        msgs = st.session_state.messages[:4]

        def gen_title():
            title = generate_title(msgs)
            update_session_title(session_id, title)

        t = threading.Thread(target=gen_title, daemon=True)
        t.start()


# ── 主界面 ─────────────────────────────────────────────────
def main():
    render_sidebar()

    # 若无当前会话，自动创建
    if st.session_state.current_session_id is None:
        sid = create_session()
        st.session_state.current_session_id = sid
        st.session_state.messages = []

    # 页面标题
    st.markdown(
        '<h2 style="color:#e2e8f0; margin-bottom:0.5rem;">⚡ Hydrogen Chat</h2>',
        unsafe_allow_html=True
    )
    st.caption("支持网络搜索 · 知识库查询 · 数据库查询 · 图片/文件上传")
    st.divider()

    # 渲染历史消息
    render_messages()

    # 文件上传区
    with st.expander("📎 上传文件或图片", expanded=False):
        uploaded_files = st.file_uploader(
            "支持图片、PDF、文本等格式",
            accept_multiple_files=True,
            type=["png", "jpg", "jpeg", "gif", "webp", "pdf", "txt", "csv", "json", "py", "md"],
            key="file_uploader"
        )
        if uploaded_files:
            st.success(f"已选择 {len(uploaded_files)} 个文件")
            for f in uploaded_files:
                st.caption(f"• {f.name} ({f.size // 1024} KB)")
    
    # 聊天输入框
    user_input = st.chat_input("输入消息，按 Enter 发送...")

    if user_input:
        files = st.session_state.get("file_uploader") or []
        stream_response(user_input, files)
        st.rerun()


if __name__ == "__main__":
    main()
