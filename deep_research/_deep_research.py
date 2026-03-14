"""
_deep_research.py — Streamlit 测试前端
功能：
- 启动新的深度研究
- 查看历史研究记录
- 以思维导图形式展示研究进展（Researcher_list + task_list）
- 质询对话框（doubt.jsonl）
- 渲染最终调研报告（research_report.md）
"""

import json
import time
from datetime import datetime

import requests
import streamlit as st

API_BASE = "http://localhost:3031"

st.set_page_config(
    page_title="Deep Research",
    page_icon="🔬",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ==================== 样式 ====================
st.markdown("""
<style>
.main-title { font-size: 2rem; font-weight: 700; color: #1a1a2e; margin-bottom: 0.2rem; }
.sub-title  { font-size: 1rem; color: #666; margin-bottom: 1.5rem; }
.stage-badge {
    display: inline-block; padding: 2px 10px; border-radius: 12px;
    font-size: 0.8rem; font-weight: 600;
}
.badge-running  { background: #fff3cd; color: #856404; }
.badge-completed{ background: #d1e7dd; color: #0a3622; }
.badge-failed   { background: #f8d7da; color: #842029; }
.badge-pending  { background: #e2e3e5; color: #41464b; }
.researcher-card {
    border: 1px solid #dee2e6; border-radius: 8px;
    padding: 12px 16px; margin-bottom: 8px;
    background: #f8f9fa;
}
.task-item {
    border-left: 3px solid #0d6efd; padding: 6px 12px;
    margin: 4px 0; background: #fff; border-radius: 0 6px 6px 0;
    font-size: 0.85rem;
}
.doubt-card {
    border: 1px solid #ffc107; border-radius: 8px;
    padding: 12px; margin-bottom: 10px; background: #fffdf0;
}
</style>
""", unsafe_allow_html=True)


# ==================== 工具函数 ====================

def _api(method: str, path: str, **kwargs):
    try:
        resp = getattr(requests, method)(f"{API_BASE}{path}", timeout=10, **kwargs)
        resp.raise_for_status()
        return resp
    except requests.exceptions.ConnectionError:
        st.error("无法连接到 Deep Research 服务（localhost:3031），请先启动 main.py")
        return None
    except Exception as e:
        st.error(f"API 请求失败: {e}")
        return None


def _badge(status: str) -> str:
    cls = {
        "running": "badge-running",
        "completed": "badge-completed",
        "failed": "badge-failed",
        "pending": "badge-pending",
    }.get(status, "badge-pending")
    label = {"running": "运行中", "completed": "已完成", "failed": "失败", "pending": "等待中"}.get(status, status)
    return f'<span class="stage-badge {cls}">{label}</span>'


def _fmt_time(iso: str) -> str:
    if not iso:
        return "-"
    try:
        return datetime.fromisoformat(iso).strftime("%m-%d %H:%M:%S")
    except Exception:
        return iso


# ==================== 侧边栏：历史项目 ====================

def render_sidebar():
    st.sidebar.markdown("## 🔬 Deep Research")
    st.sidebar.markdown("---")

    if st.sidebar.button("🔄 刷新项目列表", use_container_width=True):
        st.rerun()

    resp = _api("get", "/projects")
    if not resp:
        return None

    projects = resp.json().get("projects", [])

    if not projects:
        st.sidebar.info("暂无历史研究记录")
        return None

    st.sidebar.markdown("### 📂 历史研究")
    selected = None
    for p in projects:
        status_icon = {"completed": "✅", "running": "⏳", "failed": "❌"}.get(p.get("status", ""), "❓")
        label = p.get("question", p["project_dir"])[:30] + ("..." if len(p.get("question", "")) > 30 else "")
        if st.sidebar.button(
            f"{status_icon} {label}",
            key=f"proj_{p['project_dir']}",
            use_container_width=True,
        ):
            selected = p["project_dir"]
            st.session_state["selected_project"] = selected

    return st.session_state.get("selected_project")


# ==================== 主页面 ====================

def render_new_research():
    st.markdown('<div class="main-title">🔬 Deep Research</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub-title">基于多智能体的深度研究框架</div>', unsafe_allow_html=True)

    with st.form("research_form"):
        question = st.text_area(
            "请输入研究问题",
            placeholder="例如：分析2025年全球人工智能芯片市场的竞争格局与未来趋势",
            height=100,
        )
        submitted = st.form_submit_button("🚀 开始深度研究", use_container_width=True, type="primary")

    if submitted and question.strip():
        resp = _api("post", "/research", json={"question": question.strip()})
        if resp:
            data = resp.json()
            project_dir = data.get("project_dir")
            st.success(f"研究已启动！项目ID：`{project_dir}`")
            st.session_state["selected_project"] = project_dir
            st.rerun()


def render_project_detail(project_dir: str):
    # 获取进度
    resp = _api("get", f"/progress/{project_dir}")
    if not resp:
        return
    progress = resp.json()

    # 获取详情
    detail_resp = _api("get", f"/detail/{project_dir}")
    detail = detail_resp.json() if detail_resp else {}

    status = progress.get("status", "unknown")
    question = progress.get("question", "")

    # 标题区
    col1, col2 = st.columns([3, 1])
    with col1:
        st.markdown(f"### 📋 {project_dir}")
        if progress.get("researcher_list"):
            first = progress["researcher_list"][0]
            q = first.get("background", "") or progress.get("question", "")
        else:
            q = ""
    with col2:
        st.markdown(_badge(status), unsafe_allow_html=True)
        if status == "running":
            if st.button("🔄 刷新", key="refresh_btn"):
                st.rerun()
            st.caption(f"自动刷新中...")

    # 进度统计
    prog = progress.get("progress", {})
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("子研究总数", prog.get("total_sub_researches", 0))
    c2.metric("已完成", prog.get("completed", 0))
    c3.metric("运行中", prog.get("running", 0))
    c4.metric("等待中", prog.get("pending", 0))

    if progress.get("error"):
        st.error(f"错误信息：{progress['error']}")

    st.markdown("---")

    # Tabs
    tab_mind, tab_doubt, tab_report = st.tabs(["🗺️ 研究进展", "💬 质询记录", "📄 研究报告"])

    # ---- Tab1: 思维导图 ----
    with tab_mind:
        render_mind_map(detail, progress)

    # ---- Tab2: 质询记录 ----
    with tab_doubt:
        render_doubts(detail.get("doubts", []))

    # ---- Tab3: 研究报告 ----
    with tab_report:
        render_report(project_dir, progress)

    # 自动刷新（运行中时每5秒刷新）
    if status == "running":
        time.sleep(5)
        st.rerun()


def render_mind_map(detail: dict, progress: dict):
    researcher_list = detail.get("researcher_list", progress.get("researcher_list", []))
    researcher_tasks = detail.get("researcher_tasks", {})

    if not researcher_list:
        st.info("暂无研究数据")
        return

    # 按 Researcher 分组
    grouped: dict[str, list] = {}
    for sub in researcher_list:
        r_id = sub.get("researcher_id", "Unknown")
        grouped.setdefault(r_id, []).append(sub)

    for r_id, subs in sorted(grouped.items()):
        with st.expander(f"👤 {r_id}（{len(subs)} 个子研究）", expanded=True):
            for sub in subs:
                status = sub.get("status", "pending")
                st.markdown(
                    f'<div class="researcher-card">'
                    f'<b>{sub.get("goal", "（无目标）")}</b> {_badge(status)}<br>'
                    f'<small>背景：{sub.get("background", "")[:100]}...</small><br>'
                    f'<small>开始：{_fmt_time(sub.get("start_time"))} | 结束：{_fmt_time(sub.get("end_time"))}</small>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

                # 展示该 Researcher 的任务列表
                tasks = researcher_tasks.get(r_id, [])
                sub_id = sub.get("sub_research_id")
                related_tasks = [t for t in tasks if t.get("sub_research_id") == sub_id]

                if related_tasks:
                    with st.expander(f"  📋 任务详情（{len(related_tasks)} 个）", expanded=False):
                        for task in related_tasks:
                            t_status = task.get("status", "pending")
                            agent = task.get("agent_class", "Unknown")
                            goal = task.get("goal", "")
                            st.markdown(
                                f'<div class="task-item">'
                                f'<b>[{agent}]</b> {goal[:80]} {_badge(t_status)}'
                                f'</div>',
                                unsafe_allow_html=True,
                            )


def render_doubts(doubts: list):
    if not doubts:
        st.info("暂无质疑记录")
        return

    st.markdown(f"共 **{len(doubts)}** 条质疑记录")

    for doubt in doubts:
        accepted = doubt.get("accepted")
        if accepted is True:
            icon = "✅"
            border_color = "#198754"
        elif accepted is False:
            icon = "❌"
            border_color = "#dc3545"
        else:
            icon = "⏳"
            border_color = "#ffc107"

        with st.container():
            st.markdown(
                f'<div class="doubt-card" style="border-color: {border_color};">'
                f'<b>{icon} 质疑者：{doubt.get("doubter", "?")} → 被质疑：{doubt.get("doubted", "?")}</b><br>'
                f'<small>任务ID：{doubt.get("task_id", "")}</small>'
                f'</div>',
                unsafe_allow_html=True,
            )

            col1, col2 = st.columns(2)
            with col1:
                st.markdown("**质疑内容**")
                st.info(doubt.get("doubt_content", "（无）"))
            with col2:
                st.markdown("**回答内容**")
                answer = doubt.get("answer_content", "")
                if answer:
                    st.success(answer)
                else:
                    st.warning("尚未回答")

            if doubt.get("reason"):
                st.caption(f"判断理由：{doubt['reason']}")

            st.markdown("---")


def render_report(project_dir: str, progress: dict):
    if not progress.get("has_report"):
        if progress.get("status") == "running":
            st.info("研究进行中，报告生成后将在此显示...")
        else:
            st.warning("报告尚未生成")
        return

    resp = _api("get", f"/report/{project_dir}")
    if not resp:
        return

    report_name = progress.get("report_name", "research_report.md")
    st.markdown(f"**报告文件：** `{report_name}`")
    st.download_button(
        label="⬇️ 下载报告",
        data=resp.text,
        file_name=report_name,
        mime="text/markdown",
    )
    st.markdown("---")
    st.markdown(resp.text)


# ==================== 主入口 ====================

def main():
    selected_project = render_sidebar()

    if selected_project:
        render_project_detail(selected_project)
    else:
        render_new_research()


if __name__ == "__main__":
    main()
