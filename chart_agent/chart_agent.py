"""
智能图表绘制 - Streamlit 测试前端
连接后端服务：http://localhost:9621
"""

import time
import requests
import streamlit as st
from pathlib import Path

BACKEND_URL = "http://localhost:9621"

# ==================== 页面配置 ====================
st.set_page_config(
    page_title="智能图表绘制助手",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.title("📊 智能图表绘制助手")
st.caption("基于 Gemini 的数据库查询与图表生成系统")

# ==================== Session State 初始化 ====================
if "project_name" not in st.session_state:
    st.session_state.project_name = None

if "messages" not in st.session_state:
    st.session_state.messages = []  # {"role": "user"/"assistant", "content": str, "images": [url]}

if "progress_records" not in st.session_state:
    st.session_state.progress_records = []

# ==================== 侧边栏 ====================
with st.sidebar:
    st.header("⚙️ 会话管理")

    if st.session_state.project_name:
        st.success(f"当前项目：\n`{st.session_state.project_name}`")
        if st.button("🔄 开启新会话", use_container_width=True):
            st.session_state.project_name = None
            st.session_state.messages = []
            st.session_state.progress_records = []
            st.rerun()
    else:
        st.info("尚未开始会话，发送第一条消息后自动创建项目。")

    st.divider()
    st.header("📋 执行进度")

    if st.session_state.project_name:
        if st.button("🔃 刷新进度", use_container_width=True):
            try:
                resp = requests.get(
                    f"{BACKEND_URL}/progress/{st.session_state.project_name}",
                    timeout=10
                )
                if resp.status_code == 200:
                    st.session_state.progress_records = resp.json().get("records", [])
            except Exception as e:
                st.error(f"获取进度失败：{e}")

    if st.session_state.progress_records:
        # 状态颜色映射
        STATUS_ICONS = {
            "user_turn": "👤",
            "model_turn": "🤖",
            "正在思考": "🤔",
            "思考完成": "✅",
            "生成SQL": "🔧",
            "重新生成SQL": "🔄",
            "SQL已生成": "📝",
            "执行SQL": "▶️",
            "执行SQL失败": "❌",
            "SQL执行成功": "✅",
            "验证查询结果": "🔍",
            "验证完成": "✅",
            "验证失败": "❌",
            "生成图表": "📊",
            "图表生成完成": "🎉",
            "图表生成失败": "❌",
            "SQL流程失败，降级为直接回答": "⚠️",
            "SQL生成超出最大重试次数": "🚫",
            "生成SQL失败": "❌",
        }

        for record in st.session_state.progress_records:
            status = record.get("status", "")
            icon = STATUS_ICONS.get(status, "•")
            ts = record.get("timestamp", "")[:19].replace("T", " ")

            if status in ("user_turn", "model_turn"):
                continue  # 对话内容在主区域展示

            with st.expander(f"{icon} {status}", expanded=False):
                st.caption(ts)
                for k, v in record.items():
                    if k not in ("status", "timestamp"):
                        st.text(f"{k}: {v}")
    else:
        st.caption("暂无执行记录")

# ==================== 主聊天区域 ====================

# 展示历史消息
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        for img_url in msg.get("images", []):
            full_url = f"{BACKEND_URL}{img_url}"
            st.image(full_url, use_container_width=True)

# ==================== 输入区域 ====================
with st.container():
    uploaded_files = st.file_uploader(
        "上传图片或文件（可选，支持 jpg/png/txt/csv）",
        accept_multiple_files=True,
        type=["jpg", "jpeg", "png", "gif", "webp", "txt", "csv"],
        key="file_uploader"
    )

user_input = st.chat_input("请输入您的问题，例如：查询最近10篇文章的标题和年份，并绘制柱状图")

if user_input:
    # ---------- 展示用户消息 ----------
    with st.chat_message("user"):
        st.markdown(user_input)
        if uploaded_files:
            for f in uploaded_files:
                if f.type.startswith("image"):
                    st.image(f, caption=f.name, width=200)
                else:
                    st.caption(f"📎 {f.name}")

    st.session_state.messages.append({
        "role": "user",
        "content": user_input,
        "images": []
    })

    # ---------- 调用后端 ----------
    with st.chat_message("assistant"):
        status_placeholder = st.empty()
        status_placeholder.info("⏳ 正在处理，请稍候...")

        try:
            # 构建 multipart 请求
            form_data = {"user_input": user_input}
            if st.session_state.project_name:
                form_data["project_name"] = st.session_state.project_name

            files_payload = []
            if uploaded_files:
                for f in uploaded_files:
                    f.seek(0)
                    files_payload.append(("files", (f.name, f.read(), f.type)))

            # 轮询进度的同时等待响应
            with st.spinner("Gemini 正在分析..."):
                response = requests.post(
                    f"{BACKEND_URL}/chat",
                    data=form_data,
                    files=files_payload if files_payload else None,
                    timeout=300
                )

            if response.status_code == 200:
                result = response.json()

                # 更新项目名
                st.session_state.project_name = result["project_name"]

                reply_text = result.get("reply_text", "")
                image_urls = result.get("image_urls", [])
                need_db = result.get("need_db", False)
                error = result.get("error")

                status_placeholder.empty()

                # 展示标签
                cols = st.columns(3)
                with cols[0]:
                    st.caption(f"🗄️ 数据库查询：{'是' if need_db else '否'}")
                with cols[1]:
                    st.caption(f"📁 项目：{result['project_name']}")
                with cols[2]:
                    if result.get("csv_path"):
                        st.caption("📊 CSV 已生成")

                # 展示 AI 回复
                if reply_text:
                    st.markdown(reply_text)

                # 展示生成图片
                for img_url in image_urls:
                    full_url = f"{BACKEND_URL}{img_url}"
                    st.image(full_url, use_container_width=True)

                if error and not reply_text:
                    st.error(f"处理出错：{error}")

                # 保存到消息历史
                st.session_state.messages.append({
                    "role": "assistant",
                    "content": reply_text or ("处理出错：" + error if error else ""),
                    "images": image_urls
                })

                # 自动刷新进度记录
                try:
                    prog_resp = requests.get(
                        f"{BACKEND_URL}/progress/{result['project_name']}",
                        timeout=10
                    )
                    if prog_resp.status_code == 200:
                        st.session_state.progress_records = prog_resp.json().get("records", [])
                except Exception:
                    pass

            else:
                status_placeholder.empty()
                err_msg = f"后端返回错误 {response.status_code}：{response.text[:300]}"
                st.error(err_msg)
                st.session_state.messages.append({
                    "role": "assistant",
                    "content": err_msg,
                    "images": []
                })

        except requests.exceptions.ConnectionError:
            status_placeholder.empty()
            st.error(
                "❌ 无法连接到后端服务（http://localhost:9621）\n\n"
                "请先启动后端：`python main.py`"
            )
        except requests.exceptions.Timeout:
            status_placeholder.empty()
            st.error("⏱️ 请求超时（300s），请稍后重试或简化问题。")
        except Exception as e:
            status_placeholder.empty()
            st.error(f"未知错误：{e}")
