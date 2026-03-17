"""
自动化多模态构建管线 - 数据收集模块
通过 Gemini 分析研究方向关键词，爬取相关信息并下载 arxiv 论文
"""

import os
import json
import time
import asyncio
import requests
import streamlit as st
from pathlib import Path
from datetime import datetime

from google import genai
from google.genai import types
from dotenv import load_dotenv


# 加载 .env 文件中的环境变量
load_dotenv()
# ==================== 配置 ====================
# 本地代理端口（Google_ai2dify_port6773.py 提供）
GEMINI_BASE_URL = "http://localhost:6773"
GEMINI_MODEL = "gemini-3.1-pro-preview"

DATA_DIR = Path(__file__).parent / "data_collection"
DATA_JSONL = DATA_DIR / "data.jsonl"
PAPERS_DIR = DATA_DIR / "papers"

DATA_DIR.mkdir(parents=True, exist_ok=True)
PAPERS_DIR.mkdir(parents=True, exist_ok=True)

# ==================== Gemini 客户端初始化 ====================
def get_gemini_client() -> genai.Client:
    """初始化 Gemini 客户端，指向本地代理端口"""
    api_key = os.getenv("GEMINI_API_KEY", "placeholder")
    client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(base_url=GEMINI_BASE_URL)
    )
    return client


# ==================== 关键词生成（三次分类询问）====================
def query_prerequisite_keywords(client: genai.Client, research_direction: str) -> list[str]:
    """询问该研究方向的前提知识关键词"""
    prompt = f"""
你是一位学术研究专家。用户的研究方向是："{research_direction}"

请列出学习该方向所必须掌握的【前提知识】关键词（基础概念、数学工具、基础理论等）。
要求：
- 返回 JSON 格式：{{"keywords": ["关键词1", "关键词2", ...]}}
- 关键词使用英文（便于检索）
- 数量：10~20 个
- 只返回 JSON，不要其他解释
"""
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json"
        )
    )
    data = json.loads(response.text)
    return data.get("keywords", [])


def query_domain_keywords(client: genai.Client, research_direction: str) -> list[str]:
    """询问该研究方向的核心领域关键词"""
    prompt = f"""
你是一位学术研究专家。用户的研究方向是："{research_direction}"

请列出该研究方向的【核心领域】关键词（主流方法、重要模型、核心技术、代表性论文主题等）。
要求：
- 返回 JSON 格式：{{"keywords": ["关键词1", "关键词2", ...]}}
- 关键词使用英文（便于检索）
- 数量：10~20 个
- 只返回 JSON，不要其他解释
"""
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json"
        )
    )
    data = json.loads(response.text)
    return data.get("keywords", [])


def query_crossdomain_keywords(client: genai.Client, research_direction: str) -> list[str]:
    """询问该研究方向的交叉方向关键词"""
    prompt = f"""
你是一位学术研究专家。用户的研究方向是："{research_direction}"

请列出与该研究方向相关的【交叉学科/交叉方向】关键词（与其他领域结合的新兴方向、应用场景等）。
要求：
- 返回 JSON 格式：{{"keywords": ["关键词1", "关键词2", ...]}}
- 关键词使用英文（便于检索）
- 数量：10~20 个
- 只返回 JSON，不要其他解释
"""
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json"
        )
    )
    data = json.loads(response.text)
    return data.get("keywords", [])


def collect_all_keywords(research_direction: str, progress_callback=None) -> dict:
    """
    三次调用 Gemini，分别获取前提知识、领域方向、交叉方向关键词
    返回结构化关键词字典
    """
    client = get_gemini_client()
    result = {
        "research_direction": research_direction,
        "prerequisite": [],
        "domain": [],
        "crossdomain": [],
        "all": []
    }

    if progress_callback:
        progress_callback("正在询问前提知识关键词...", 0.1)
    result["prerequisite"] = query_prerequisite_keywords(client, research_direction)

    if progress_callback:
        progress_callback("正在询问核心领域关键词...", 0.4)
    result["domain"] = query_domain_keywords(client, research_direction)

    if progress_callback:
        progress_callback("正在询问交叉方向关键词...", 0.7)
    result["crossdomain"] = query_crossdomain_keywords(client, research_direction)

    # 合并去重
    all_kw = list(dict.fromkeys(
        result["prerequisite"] + result["domain"] + result["crossdomain"]
    ))
    result["all"] = all_kw

    if progress_callback:
        progress_callback("关键词生成完成！", 1.0)

    return result


# ==================== 网络爬虫：收集相关信息 ====================
def search_and_scrape_keyword(keyword: str, max_results: int = 5) -> list[dict]:
    """
    使用 DuckDuckGo 搜索关键词，抓取摘要信息
    返回结构化条目列表
    """
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        return [{"error": "请安装 duckduckgo-search: pip install duckduckgo-search"}]

    results = []
    try:
        with DDGS() as ddgs:
            for r in ddgs.text(keyword, max_results=max_results):
                results.append({
                    "keyword": keyword,
                    "title": r.get("title", ""),
                    "url": r.get("href", ""),
                    "snippet": r.get("body", ""),
                    "source": "duckduckgo",
                    "collected_at": datetime.now().isoformat()
                })
    except Exception as e:
        results.append({
            "keyword": keyword,
            "error": str(e),
            "source": "duckduckgo",
            "collected_at": datetime.now().isoformat()
        })
    return results


def collect_web_data(keywords: list[str], progress_callback=None) -> int:
    """
    遍历所有关键词，爬取信息，追加写入 data.jsonl
    返回写入的条目总数
    """
    total_written = 0
    n = len(keywords)

    with open(DATA_JSONL, "a", encoding="utf-8") as f:
        for i, kw in enumerate(keywords):
            if progress_callback:
                progress_callback(f"爬取关键词 [{i+1}/{n}]: {kw}", i / n)

            entries = search_and_scrape_keyword(kw)
            for entry in entries:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                total_written += 1

            # 避免请求过于频繁
            time.sleep(0.5)

    if progress_callback:
        progress_callback(f"网络数据收集完成，共写入 {total_written} 条", 1.0)

    return total_written


# ==================== arxiv 论文下载 ====================
def search_arxiv(keyword: str, max_results: int = 3) -> list[dict]:
    """搜索 arxiv 并返回论文元数据列表"""
    try:
        import arxiv
    except ImportError:
        return []

    search = arxiv.Search(
        query=keyword,
        max_results=max_results,
        sort_by=arxiv.SortCriterion.Relevance
    )
    papers = []
    for result in search.results():
        papers.append({
            "arxiv_id": result.entry_id.split("/")[-1],
            "title": result.title,
            "authors": [a.name for a in result.authors],
            "summary": result.summary,
            "published": result.published.isoformat(),
            "pdf_url": result.pdf_url,
            "keyword": keyword
        })
    return papers


def download_arxiv_papers(keywords: list[str], max_per_keyword: int = 2, progress_callback=None) -> int:
    """
    根据关键词搜索并下载 arxiv 论文 PDF
    返回成功下载的论文数量
    """
    try:
        import arxiv
    except ImportError:
        if progress_callback:
            progress_callback("请安装 arxiv 库: pip install arxiv", 0)
        return 0

    downloaded = 0
    n = len(keywords)

    for i, kw in enumerate(keywords):
        if progress_callback:
            progress_callback(f"搜索 arxiv [{i+1}/{n}]: {kw}", i / n)

        papers = search_arxiv(kw, max_results=max_per_keyword)
        for paper in papers:
            arxiv_id = paper["arxiv_id"]
            pdf_path = PAPERS_DIR / f"{arxiv_id}.pdf"

            # 已下载则跳过
            if pdf_path.exists():
                continue

            try:
                # 记录元数据到 jsonl
                with open(DATA_JSONL, "a", encoding="utf-8") as f:
                    meta = {**paper, "source": "arxiv", "collected_at": datetime.now().isoformat()}
                    f.write(json.dumps(meta, ensure_ascii=False) + "\n")

                # 下载 PDF
                resp = requests.get(paper["pdf_url"], timeout=60)
                if resp.status_code == 200:
                    pdf_path.write_bytes(resp.content)
                    downloaded += 1
                    if progress_callback:
                        progress_callback(f"已下载: {paper['title'][:50]}...", (i + 0.5) / n)

                time.sleep(1)  # 礼貌延迟

            except Exception as e:
                if progress_callback:
                    progress_callback(f"下载失败 {arxiv_id}: {e}", (i + 0.5) / n)

    if progress_callback:
        progress_callback(f"论文下载完成，共下载 {downloaded} 篇", 1.0)

    return downloaded


# ==================== Streamlit 前端 ====================
def render_keyword_badges(keywords: list[str], color: str = "#1f77b4"):
    """渲染关键词标签"""
    badges_html = " ".join(
        f'<span style="background:{color};color:white;padding:3px 10px;'
        f'border-radius:12px;margin:3px;display:inline-block;font-size:13px;">{kw}</span>'
        for kw in keywords
    )
    st.markdown(badges_html, unsafe_allow_html=True)


def main():
    st.set_page_config(
        page_title="Auto GRPO - 数据收集",
        page_icon="🔬",
        layout="wide"
    )

    st.title("🔬 自动化多模态构建管线 · 数据收集")
    st.caption("关键词生成 → 网络爬取 → 论文下载")

    if "keywords_result" not in st.session_state:
        st.session_state.keywords_result = None
    if "run_web_crawl" not in st.session_state:
        st.session_state.run_web_crawl = False
    if "run_paper_download" not in st.session_state:
        st.session_state.run_paper_download = False

    # ── 侧边栏 ──────────────────────────────────────────────
    with st.sidebar:
        st.header("🔬 数据收集管线")

        # 研究方向输入
        research_direction = st.text_input(
            "研究方向",
            placeholder="例如：Reinforcement Learning from Human Feedback",
            help="支持中英文，AI 会自动生成英文检索关键词"
        )

        st.divider()

        # ── Part 1：关键词生成 ──
        st.subheader("① 关键词生成")
        gen_btn = st.button(
            "🚀 生成关键词",
            disabled=not research_direction,
            use_container_width=True
        )

        st.divider()

        # ── Part 2：网络爬取 ──
        st.subheader("② 网络爬取")
        max_web_per_kw = st.slider("每个关键词爬取网页数", 1, 10, 5)
        has_keywords = st.session_state.keywords_result is not None
        if st.button(
            "🕷️ 开始爬取",
            disabled=not has_keywords,
            use_container_width=True,
            help="请先生成关键词"
        ):
            st.session_state.run_web_crawl = True

        st.divider()

        # ── Part 3：论文下载 ──
        st.subheader("③ 论文下载")
        max_paper_per_kw = st.slider("每个关键词下载论文数", 1, 5, 2)
        if st.button(
            "📥 开始下载论文",
            disabled=not has_keywords,
            use_container_width=True,
            help="请先生成关键词"
        ):
            st.session_state.run_paper_download = True

        st.divider()

        # ── 统计信息 ──
        st.caption("📊 当前数据统计")
        col_a, col_b = st.columns(2)
        with col_a:
            jsonl_count = 0
            if DATA_JSONL.exists():
                with open(DATA_JSONL, "r", encoding="utf-8") as f:
                    jsonl_count = sum(1 for _ in f)
            st.metric("已收集条目", jsonl_count)
        with col_b:
            st.metric("已下载论文", len(list(PAPERS_DIR.glob("*.pdf"))))

    # ── 主区域 ──────────────────────────────────────────────

    # ── Section 1：关键词生成结果 ──
    st.header("🔑 关键词生成")

    if gen_btn and research_direction:
        progress_bar = st.progress(0)
        status_text = st.empty()

        def kw_progress(msg, val):
            status_text.text(msg)
            progress_bar.progress(val)

        with st.spinner("正在调用 Gemini 分析研究方向..."):
            try:
                result = collect_all_keywords(research_direction, progress_callback=kw_progress)
                st.session_state.keywords_result = result
                st.session_state.run_web_crawl = False
                st.session_state.run_paper_download = False
                st.success(f"✅ 共生成 {len(result['all'])} 个关键词")
            except Exception as e:
                st.error(f"❌ 关键词生成失败：{e}")

    if st.session_state.keywords_result:
        kw_result = st.session_state.keywords_result
        st.caption(f"研究方向：**{kw_result['research_direction']}**　|　共 {len(kw_result['all'])} 个关键词（已去重）")

        tab1, tab2, tab3, tab4 = st.tabs(["📚 前提知识", "🎯 核心领域", "🔀 交叉方向", "📋 全部关键词"])
        with tab1:
            st.subheader(f"前提知识（{len(kw_result['prerequisite'])} 个）")
            render_keyword_badges(kw_result["prerequisite"], "#2196F3")
        with tab2:
            st.subheader(f"核心领域（{len(kw_result['domain'])} 个）")
            render_keyword_badges(kw_result["domain"], "#4CAF50")
        with tab3:
            st.subheader(f"交叉方向（{len(kw_result['crossdomain'])} 个）")
            render_keyword_badges(kw_result["crossdomain"], "#FF9800")
        with tab4:
            st.subheader(f"全部关键词（{len(kw_result['all'])} 个）")
            render_keyword_badges(kw_result["all"], "#9C27B0")
            st.code("\n".join(kw_result["all"]), language="text")
    else:
        st.info("请在左侧输入研究方向并点击「生成关键词」")

    st.divider()

    # ── Section 2：网络爬取 ──
    st.header("🌐 网络信息爬取")

    if st.session_state.run_web_crawl and st.session_state.keywords_result:
        st.session_state.run_web_crawl = False
        kw_result = st.session_state.keywords_result
        st.caption(f"对 {len(kw_result['all'])} 个关键词进行 DuckDuckGo 搜索，结果保存至 `data.jsonl`")

        progress_bar2 = st.progress(0)
        status_text2 = st.empty()
        log_area = st.empty()
        logs = []

        def web_progress(msg, val):
            status_text2.text(msg)
            progress_bar2.progress(val)
            logs.append(msg)
            log_area.text_area("爬取日志", "\n".join(logs[-20:]), height=150)

        with st.spinner("爬取中..."):
            try:
                count = collect_web_data(kw_result["all"], progress_callback=web_progress)
                st.success(f"✅ 爬取完成，共写入 {count} 条到 `data.jsonl`")
            except Exception as e:
                st.error(f"❌ 爬取失败：{e}")
    elif st.session_state.keywords_result:
        st.info("点击左侧「开始爬取」按钮启动网络数据收集")
    else:
        st.info("请先完成关键词生成")

    # 数据预览
    if DATA_JSONL.exists():
        with open(DATA_JSONL, "r", encoding="utf-8") as f:
            lines = [l for l in f.readlines() if l.strip()]
        if lines:
            with st.expander(f"查看已收集数据（共 {len(lines)} 条，显示最新 10 条）"):
                st.json([json.loads(l) for l in lines[-10:]])

    st.divider()

    # ── Section 3：论文下载 ──
    st.header("📄 arxiv 论文下载")

    if st.session_state.run_paper_download and st.session_state.keywords_result:
        st.session_state.run_paper_download = False
        kw_result = st.session_state.keywords_result
        st.caption(f"根据 {len(kw_result['all'])} 个关键词搜索并下载 arxiv 论文 PDF")

        progress_bar3 = st.progress(0)
        status_text3 = st.empty()
        log_area3 = st.empty()
        logs3 = []

        def paper_progress(msg, val):
            status_text3.text(msg)
            progress_bar3.progress(val)
            logs3.append(msg)
            log_area3.text_area("下载日志", "\n".join(logs3[-20:]), height=150)

        with st.spinner("下载中..."):
            try:
                count = download_arxiv_papers(
                    kw_result["all"],
                    max_per_keyword=max_paper_per_kw,
                    progress_callback=paper_progress
                )
                st.success(f"✅ 论文下载完成，共下载 {count} 篇 PDF")
            except Exception as e:
                st.error(f"❌ 下载失败：{e}")
    elif st.session_state.keywords_result:
        st.info("点击左侧「开始下载论文」按钮启动 arxiv 论文下载")
    else:
        st.info("请先完成关键词生成")

    # 已下载论文列表
    pdf_files = list(PAPERS_DIR.glob("*.pdf"))
    if pdf_files:
        with st.expander(f"已下载论文（{len(pdf_files)} 篇）"):
            for pdf in pdf_files:
                st.text(f"📄 {pdf.name}")


if __name__ == "__main__":
    main()
