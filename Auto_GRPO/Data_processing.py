"""
自动化多模态构建管线 - 数据处理模块
1. PDF 分段处理（PyMuPDF）→ database.jsonl
2. Markdown/TXT 处理 → database.jsonl
3. Gemini 生成 GRPO 数据集 → dataset.jsonl
"""

import os
import re
import json
import time
import uuid
import asyncio
import base64
import hashlib
import threading
from pathlib import Path
from datetime import datetime
from typing import Optional

import fitz  # PyMuPDF
import streamlit as st
from dotenv import load_dotenv

from google import genai
from google.genai import types

load_dotenv()

# ==================== 路径配置 ====================
BASE_DIR       = Path(__file__).parent / "data_collection"
PAPERS_DIR = Path("E:/氢聊2.0知识库/杜承昊/批batch1_中1+英1")
IMAGES_DIR     = BASE_DIR / "images"
DATABASE_JSONL = BASE_DIR / "database.jsonl"
DATASET_DIR    = BASE_DIR / "dataset"
DATASET_JSONL  = DATASET_DIR / "dataset.jsonl"
PROGRESS_FILE  = BASE_DIR / ".processing_progress.json"
DATASET_PROGRESS_FILE = BASE_DIR / ".dataset_progress.json"

for d in [BASE_DIR, PAPERS_DIR, IMAGES_DIR, DATASET_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# ==================== 模型配置 ====================
PRIMARY_MODEL   = "gemini-3.1-flash-lite-preview"
FALLBACK_MODEL  = "gemini-2.5-flash"
GEMINI_BASE_URL = "http://localhost:6773"

# ==================== 雪花算法 ID 生成 ====================
class SnowflakeID:
    """简化版雪花算法，生成唯一图片文件名"""
    _lock = threading.Lock()
    _sequence = 0
    _last_ms = -1

    @classmethod
    def next_id(cls) -> str:
        with cls._lock:
            ms = int(time.time() * 1000)
            if ms == cls._last_ms:
                cls._sequence = (cls._sequence + 1) & 0xFFF
                if cls._sequence == 0:
                    while ms <= cls._last_ms:
                        ms = int(time.time() * 1000)
            else:
                cls._sequence = 0
            cls._last_ms = ms
            return f"{ms:013d}{cls._sequence:04d}"


# ==================== Gemini 客户端 ====================
def get_gemini_client() -> genai.Client:
    api_key = os.getenv("GEMINI_API_KEY", "placeholder")
    return genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(base_url=GEMINI_BASE_URL)
    )


# ==================== 进度持久化 ====================
def load_progress(progress_file: Path) -> set:
    """加载已处理的文件/分段 ID 集合"""
    if progress_file.exists():
        try:
            data = json.loads(progress_file.read_text(encoding="utf-8"))
            return set(data.get("done", []))
        except Exception:
            return set()
    return set()


def save_progress(progress_file: Path, done: set):
    progress_file.write_text(
        json.dumps({"done": list(done)}, ensure_ascii=False),
        encoding="utf-8"
    )


# ==================== PDF 分段处理 ====================
SEGMENT_TARGET = 5000
SEGMENT_FLEX   = 1000


def smart_split(text: str, target: int = SEGMENT_TARGET, flex: int = SEGMENT_FLEX) -> list[str]:
    """
    按 target 字符分段，在 [target-flex, target+flex] 范围内寻找换行符作为切割点；
    若找不到则直接在 target 处截断。
    """
    segments = []
    start = 0
    while start < len(text):
        end = start + target
        if end >= len(text):
            segments.append(text[start:])
            break
        # 在弹性范围内找最近的换行符
        lo = max(start, end - flex)
        hi = min(len(text), end + flex)
        sub = text[lo:hi]
        nl_pos = sub.rfind("\n")
        if nl_pos != -1:
            cut = lo + nl_pos + 1
        else:
            cut = end
        segments.append(text[start:cut])
        start = cut
    return [s for s in segments if s.strip()]


def extract_pdf_segments(pdf_path: Path) -> list[dict]:
    """
    用 PyMuPDF 处理 PDF：
    - 提取文本，按 smart_split 分段
    - 提取每段覆盖页面上的图片，用雪花算法命名保存到 images/
    返回分段列表，每项包含 text、images（路径列表）、page_range
    """
    doc = fitz.open(str(pdf_path))

    # 1. 收集每页文本和图片位置
    page_texts: list[str] = []
    page_images: list[list[dict]] = []  # [{img_path, bbox}, ...]

    for page_num, page in enumerate(doc):
        page_texts.append(page.get_text("text"))

        imgs = []
        for img_info in page.get_images(full=True):
            xref = img_info[0]
            try:
                base_image = doc.extract_image(xref)
                ext = base_image["ext"]
                img_bytes = base_image["image"]
                fname = f"{SnowflakeID.next_id()}.{ext}"
                img_path = IMAGES_DIR / fname
                img_path.write_bytes(img_bytes)
                imgs.append(str(img_path))
            except Exception:
                pass
        page_images.append(imgs)

    doc.close()

    # 2. 拼接全文，记录每页起止字符位置
    full_text = ""
    page_char_ranges: list[tuple[int, int, int]] = []  # (page_num, start, end)
    for i, pt in enumerate(page_texts):
        s = len(full_text)
        full_text += pt
        page_char_ranges.append((i, s, len(full_text)))

    # 3. 分段
    raw_segments = smart_split(full_text)

    # 4. 为每个分段关联图片（找出该段覆盖的页面）
    segments = []
    cursor = 0
    for seg_text in raw_segments:
        seg_start = full_text.find(seg_text, cursor)
        seg_end = seg_start + len(seg_text)
        cursor = seg_start + 1

        covered_pages = []
        for page_num, ps, pe in page_char_ranges:
            if ps < seg_end and pe > seg_start:
                covered_pages.append(page_num)

        seg_images = []
        for pn in covered_pages:
            seg_images.extend(page_images[pn])

        segments.append({
            "source_file": str(pdf_path),
            "source_type": "pdf",
            "text": seg_text.strip(),
            "images": seg_images,
            "page_range": covered_pages,
        })

    return segments


def process_text_file(file_path: Path) -> list[dict]:
    """处理 Markdown / TXT 文件，同样按 smart_split 分段"""
    raw = file_path.read_text(encoding="utf-8", errors="ignore")
    raw_segments = smart_split(raw)
    return [
        {
            "source_file": str(file_path),
            "source_type": "text",
            "text": seg.strip(),
            "images": [],
            "page_range": [],
        }
        for seg in raw_segments
    ]


def append_to_database(records: list[dict]):
    """追加写入 database.jsonl"""
    with open(DATABASE_JSONL, "a", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def run_data_processing(progress_callback=None) -> tuple[int, int]:
    """
    扫描 papers/ 下的 PDF、MD、TXT 文件，处理后写入 database.jsonl。
    支持断点续传（基于文件路径 hash）。
    返回 (处理文件数, 总分段数)
    """
    done_set = load_progress(PROGRESS_FILE)

    all_files = (
        list(PAPERS_DIR.glob("*.pdf")) +
        list(PAPERS_DIR.glob("*.md")) +
        list(PAPERS_DIR.glob("*.txt")) +
        list(BASE_DIR.glob("*.md")) +
        list(BASE_DIR.glob("*.txt"))
    )
    all_files = [f for f in all_files if f.is_file()]

    total_files = len(all_files)
    processed_files = 0
    total_segments = 0

    for idx, file_path in enumerate(all_files):
        file_id = hashlib.md5(str(file_path).encode()).hexdigest()
        if file_id in done_set:
            if progress_callback:
                progress_callback(
                    f"跳过（已处理）：{file_path.name}",
                    (idx + 1) / total_files,
                    total_segments
                )
            continue

        if progress_callback:
            progress_callback(
                f"处理中 [{idx+1}/{total_files}]：{file_path.name}",
                idx / total_files,
                total_segments
            )

        try:
            if file_path.suffix.lower() == ".pdf":
                segments = extract_pdf_segments(file_path)
            else:
                segments = process_text_file(file_path)

            # 分段保存（每段立即写入）
            append_to_database(segments)
            total_segments += len(segments)
            processed_files += 1
            done_set.add(file_id)
            save_progress(PROGRESS_FILE, done_set)

            if progress_callback:
                progress_callback(
                    f"完成 [{idx+1}/{total_files}]：{file_path.name}（{len(segments)} 段）",
                    (idx + 1) / total_files,
                    total_segments
                )
        except Exception as e:
            if progress_callback:
                progress_callback(
                    f"错误 {file_path.name}：{e}",
                    (idx + 1) / total_files,
                    total_segments
                )

    return processed_files, total_segments


# ==================== GRPO 数据集生成 ====================
GRPO_SCHEMA = types.Schema(
    type=types.Type.OBJECT,
    required=["items"],
    properties={
        "items": types.Schema(
            type=types.Type.ARRAY,
            items=types.Schema(
                type=types.Type.OBJECT,
                required=["image_index", "prompt", "ground_truth", "reference_guideline"],
                properties={
                    "image_index": types.Schema(
                        type=types.Type.INTEGER,
                        nullable=True,
                        description="输入图片列表中的第几张（0-based），无图片时为 -1"
                    ),
                    "prompt": types.Schema(type=types.Type.STRING),
                    "ground_truth": types.Schema(type=types.Type.STRING),
                    "reference_guideline": types.Schema(type=types.Type.STRING),
                }
            )
        )
    }
)
#TODO 提示词错了，Ai以为它有上下文，问关于上下文的问题。


GRPO_SYSTEM_PROMPT = """你是一位专业的多模态数据集标注专家。
你的任务是基于给定的文本分段（以及可能附带的图片），生成用于 GRPO 强化学习微调的高质量问答数据。

要求：
1. prompt 为提问，要求给予文段的信息精炼出问题，但是注意，提问不能涉及文段的内容，回答的模型看不到文段，只能看到提问。
2. ground_truth 必须极其简短（关键词/核心结论），不超过 10 字。
3. reference_guideline 提供数条评分细则，说明如何评价推理过程和最终结论。
4. 若分段内容不足以生成有效问答，返回空 items 列表。
5. 若有图片，image_index 填写该图片在输入列表中的序号（0-based）；无图片时填 -1。
6. 可一次返回多条数据（每条数据可以包括一张图片，也可以没有图片）。
"""


async def generate_grpo_for_segment(
    client: genai.Client,
    segment: dict,
    model: str,
    semaphore: asyncio.Semaphore,
    fail_counts: dict,
) -> list[dict]:
    """
    对单个分段调用 Gemini 生成 GRPO 数据。
    主模型失败 3 次后切换备用模型，备用模型失败 3 次后抛出异常。
    """
    async with semaphore:
        contents = []

        # 构建图片内容
        image_paths = segment.get("images", [])
        for img_path in image_paths:
            try:
                img_bytes = Path(img_path).read_bytes()
                # 猜测 MIME
                ext = Path(img_path).suffix.lower().lstrip(".")
                mime_map = {"jpg": "image/jpeg", "jpeg": "image/jpeg",
                            "png": "image/png", "gif": "image/gif",
                            "webp": "image/webp"}
                mime = mime_map.get(ext, "image/png")
                contents.append(types.Part.from_bytes(data=img_bytes, mime_type=mime))
            except Exception:
                pass

        # 文本内容
        contents.append(types.Part.from_text(
            text=f"以下是文本分段内容：\n\n{segment['text'][:8000]}"
        ))

        current_model = model
        for attempt in range(6):
            try:
                response = await asyncio.to_thread(
                    client.models.generate_content,
                    model=current_model,
                    contents=[types.Content(role="user", parts=contents)],
                    config=types.GenerateContentConfig(
                        system_instruction=GRPO_SYSTEM_PROMPT,
                        response_mime_type="application/json",
                        response_schema=GRPO_SCHEMA,
                    )
                )
                raw = json.loads(response.text)
                items = raw.get("items", [])

                results = []
                for item in items:
                    idx = item.get("image_index", -1)
                    img_path = image_paths[idx] if (idx is not None and idx >= 0 and idx < len(image_paths)) else None
                    results.append({
                        "image_path": img_path,
                        "prompt": item["prompt"],
                        "ground_truth": item["ground_truth"],
                        "reference_guideline": item["reference_guideline"],
                        "source_file": segment.get("source_file", ""),
                        "generated_at": datetime.now().isoformat(),
                    })
                return results

            except Exception as e:
                # 记录失败次数，决定是否切换模型
                fail_counts[current_model] = fail_counts.get(current_model, 0) + 1
                if current_model == PRIMARY_MODEL and fail_counts[current_model] >= 3:
                    current_model = FALLBACK_MODEL
                elif current_model == FALLBACK_MODEL and fail_counts[current_model] >= 3:
                    raise RuntimeError(
                        f"主模型和备用模型均连续失败 3 次，最后错误：{e}"
                    )
                await asyncio.sleep(1)

    return []


def append_to_dataset(records: list[dict]):
    """追加写入 dataset.jsonl"""
    with open(DATASET_JSONL, "a", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


async def run_dataset_generation(
    max_parallel: int = 5,
    progress_callback=None,
) -> tuple[int, int]:
    """
    从 database.jsonl 加载分段，并行调用 Gemini 生成 GRPO 数据集。
    支持断点续传（基于分段内容 hash）。
    返回 (处理分段数, 生成条目数)
    """
    if not DATABASE_JSONL.exists():
        if progress_callback:
            progress_callback("database.jsonl 不存在，请先运行数据处理", 0, 0)
        return 0, 0

    with open(DATABASE_JSONL, "r", encoding="utf-8") as f:
        segments = [json.loads(l) for l in f if l.strip()]

    done_set = load_progress(DATASET_PROGRESS_FILE)
    client = get_gemini_client()
    semaphore = asyncio.Semaphore(max_parallel)
    fail_counts: dict = {}

    total = len(segments)
    processed = 0
    generated = 0

    async def process_one(seg: dict, seg_id: str, idx: int):
        nonlocal processed, generated
        if seg_id in done_set:
            if progress_callback:
                progress_callback(f"跳过（已生成）分段 {idx+1}/{total}", (idx+1)/total, generated)
            return

        try:
            results = await generate_grpo_for_segment(
                client, seg, PRIMARY_MODEL, semaphore, fail_counts
            )
            if results:
                append_to_dataset(results)
                generated += len(results)

            done_set.add(seg_id)
            save_progress(DATASET_PROGRESS_FILE, done_set)
            processed += 1

            if progress_callback:
                progress_callback(
                    f"分段 {idx+1}/{total} → 生成 {len(results)} 条",
                    (idx+1)/total,
                    generated
                )
        except RuntimeError as e:
            if progress_callback:
                progress_callback(f"致命错误：{e}", (idx+1)/total, generated)
            raise

    tasks = []
    for i, seg in enumerate(segments):
        seg_id = hashlib.md5(
            (seg.get("source_file", "") + seg.get("text", "")[:200]).encode()
        ).hexdigest()
        tasks.append(process_one(seg, seg_id, i))

    try:
        await asyncio.gather(*tasks)
    except RuntimeError:
        pass

    return processed, generated


# ==================== Streamlit 前端 ====================
def page_data_processing():
    """页面一：数据处理"""
    st.header("📄 数据处理")
    st.caption("将 papers/ 下的 PDF、Markdown、TXT 文件分段，提取文本和图片，写入 database.jsonl")

    # 统计
    col1, col2, col3 = st.columns(3)
    pdf_count  = len(list(PAPERS_DIR.glob("*.pdf")))
    text_count = len(list(PAPERS_DIR.glob("*.md"))) + len(list(PAPERS_DIR.glob("*.txt")))
    db_lines   = sum(1 for _ in open(DATABASE_JSONL, encoding="utf-8")) if DATABASE_JSONL.exists() else 0
    col1.metric("待处理 PDF", pdf_count)
    col2.metric("待处理文本文件", text_count)
    col3.metric("已入库分段数", db_lines)

    st.divider()

    # 断点续传提示
    done_set = load_progress(PROGRESS_FILE)
    if done_set:
        st.info(f"检测到上次进度：已处理 {len(done_set)} 个文件，可直接继续。")
        if st.button("🗑️ 清除进度，重新处理"):
            PROGRESS_FILE.unlink(missing_ok=True)
            st.rerun()

    if st.button("▶️ 开始 / 继续数据处理", use_container_width=True, type="primary"):
        progress_bar = st.progress(0.0)
        status_text  = st.empty()
        seg_counter  = st.empty()
        log_area     = st.empty()
        logs = []

        def cb(msg, pct, total_segs):
            status_text.text(msg)
            progress_bar.progress(min(pct, 1.0))
            seg_counter.caption(f"累计分段数：{total_segs}")
            logs.append(msg)
            log_area.text_area("处理日志", "\n".join(logs[-30:]), height=200)

        try:
            n_files, n_segs = run_data_processing(progress_callback=cb)
            st.success(f"✅ 处理完成：共处理 {n_files} 个文件，生成 {n_segs} 个分段")
        except Exception as e:
            st.error(f"❌ 处理失败：{e}")

    st.divider()

    # 预览
    if DATABASE_JSONL.exists():
        with open(DATABASE_JSONL, "r", encoding="utf-8") as f:
            lines = [l for l in f if l.strip()]
        if lines:
            with st.expander(f"预览 database.jsonl（共 {len(lines)} 条，显示最新 5 条）"):
                for l in lines[-5:]:
                    rec = json.loads(l)
                    st.markdown(f"**来源**：`{Path(rec['source_file']).name}`　|　**图片数**：{len(rec['images'])}　|　**字数**：{len(rec['text'])}")
                    st.text(rec["text"][:300] + ("..." if len(rec["text"]) > 300 else ""))
                    st.divider()


def page_dataset_generation():
    """页面二：数据集生成"""
    st.header("🤖 GRPO 数据集生成")
    st.caption("调用 Gemini 对每个分段生成 GRPO 微调数据，支持并行调用与断点续传")

    # 统计
    col1, col2 = st.columns(2)
    db_lines = sum(1 for _ in open(DATABASE_JSONL, encoding="utf-8")) if DATABASE_JSONL.exists() else 0
    ds_lines = sum(1 for _ in open(DATASET_JSONL, encoding="utf-8")) if DATASET_JSONL.exists() else 0
    col1.metric("待处理分段数", db_lines)
    col2.metric("已生成数据条目", ds_lines)

    st.divider()

    # 配置
    max_parallel = st.slider("并行 AI 调用数量", 1, 25, 5)
    st.caption(f"主模型：`{PRIMARY_MODEL}`　|　备用模型：`{FALLBACK_MODEL}`")
    st.caption("主模型连续失败 3 次后自动切换备用模型；备用模型再失败 3 次则终止并提示。")

    # 断点续传提示
    done_set = load_progress(DATASET_PROGRESS_FILE)
    if done_set:
        st.info(f"检测到上次进度：已处理 {len(done_set)} 个分段，可直接继续。")
        if st.button("🗑️ 清除进度，重新生成"):
            DATASET_PROGRESS_FILE.unlink(missing_ok=True)
            st.rerun()

    if not DATABASE_JSONL.exists() or db_lines == 0:
        st.warning("database.jsonl 为空，请先完成数据处理步骤。")
        return

    if st.button("▶️ 开始 / 继续生成数据集", use_container_width=True, type="primary"):
        progress_bar = st.progress(0.0)
        status_text  = st.empty()
        gen_counter  = st.empty()
        log_area     = st.empty()
        logs = []

        def cb(msg, pct, total_gen):
            status_text.text(msg)
            progress_bar.progress(min(pct, 1.0))
            gen_counter.caption(f"累计生成条目：{total_gen}")
            logs.append(msg)
            log_area.text_area("生成日志", "\n".join(logs[-30:]), height=200)

        try:
            n_segs, n_items = asyncio.run(
                run_dataset_generation(max_parallel=max_parallel, progress_callback=cb)
            )
            st.success(f"✅ 生成完成：处理 {n_segs} 个分段，生成 {n_items} 条数据集条目")
        except Exception as e:
            st.error(f"❌ 生成失败：{e}")

    st.divider()

    # 预览
    if DATASET_JSONL.exists():
        with open(DATASET_JSONL, "r", encoding="utf-8") as f:
            lines = [l for l in f if l.strip()]
        if lines:
            with st.expander(f"预览 dataset.jsonl（共 {len(lines)} 条，显示最新 3 条）"):
                for l in lines[-3:]:
                    rec = json.loads(l)
                    st.markdown(f"**image_path**：`{rec.get('image_path') or '无'}`")
                    st.markdown(f"**prompt**：{rec['prompt'][:200]}")
                    st.markdown(f"**ground_truth**：{rec['ground_truth']}")
                    st.markdown(f"**reference_guideline**：{rec['reference_guideline'][:200]}")
                    st.divider()


def main():
    st.set_page_config(
        page_title="Auto GRPO - 数据处理",
        page_icon="⚙️",
        layout="wide"
    )

    st.title("⚙️ 自动化多模态构建管线 · 数据处理")

    with st.sidebar:
        st.header("⚙️ 导航")
        page = st.radio(
            "选择页面",
            ["📄 数据处理", "🤖 数据集生成"],
            label_visibility="collapsed"
        )
        st.divider()
        st.caption(f"database.jsonl：{'✅ 存在' if DATABASE_JSONL.exists() else '❌ 未生成'}")
        st.caption(f"dataset.jsonl：{'✅ 存在' if DATASET_JSONL.exists() else '❌ 未生成'}")
        st.caption(f"图片目录：{len(list(IMAGES_DIR.glob('*')))} 张")

    if page == "📄 数据处理":
        page_data_processing()
    else:
        page_dataset_generation()


if __name__ == "__main__":
    main()
