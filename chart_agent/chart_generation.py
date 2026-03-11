"""
图表生成模块
负责：
1. 将用户输入（含多轮上下文、CSV 数据、图片文件）交给 Gemini（配置代码执行工具）
2. Gemini 自主编写并执行 Matplotlib 代码生成图表
3. 提取 AI 回复文本和生成的图片，保存图片到项目目录
4. 返回 AI 回复文本和图片路径列表
"""

import os
import json
import base64
import uuid
from pathlib import Path
from datetime import datetime

import google.generativeai as genai

# ==================== Gemini 配置 ====================
GEMINI_BASE_URL = "http://localhost:9583"
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "placeholder")

genai.configure(
    api_key=GEMINI_API_KEY,
    transport="rest",
    client_options={"api_endpoint": GEMINI_BASE_URL}
)


# ==================== 工具函数 ====================
def _append_jsonl(jsonl_path: Path, record: dict):
    """追加一条 JSON 记录到 jsonl 文件"""
    with open(jsonl_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _snowflake_id() -> str:
    """使用 uuid4 生成唯一文件名（替代雪花算法）"""
    return uuid.uuid4().hex


def _build_contents(user_input: str, context: list, csv_path: str = None) -> list:
    """
    构建 Gemini generate_content 所需的 contents 列表（多轮对话格式）。

    context 每项格式：
      {
        "role": "user" | "model",
        "text": "...",
        "files": ["path1", "path2"],   # 图片或文本文件路径
        "csv": "path/to/data.csv"       # CSV 文件路径
      }
    """
    contents = []

    for item in context:
        parts = []
        if item.get("text"):
            parts.append({"text": item["text"]})

        for file_path in item.get("files", []):
            p = Path(file_path)
            if not p.exists():
                continue
            suffix = p.suffix.lower()
            if suffix in (".jpg", ".jpeg"):
                mime = "image/jpeg"
            elif suffix == ".png":
                mime = "image/png"
            elif suffix == ".gif":
                mime = "image/gif"
            elif suffix == ".webp":
                mime = "image/webp"
            else:
                mime = "text/plain"

            with open(p, "rb") as f:
                raw = f.read()

            if mime.startswith("image"):
                parts.append({
                    "inline_data": {
                        "mime_type": mime,
                        "data": base64.b64encode(raw).decode("utf-8")
                    }
                })
            else:
                parts.append({"text": raw.decode("utf-8", errors="replace")})

        if item.get("csv"):
            csv_p = Path(item["csv"])
            if csv_p.exists():
                csv_text = csv_p.read_text(encoding="utf-8-sig")
                parts.append({"text": f"[历史CSV数据]\n{csv_text}"})

        if parts:
            contents.append({"role": item.get("role", "user"), "parts": parts})

    # 当前轮用户输入
    current_parts = []
    if csv_path:
        csv_p = Path(csv_path)
        if csv_p.exists():
            csv_text = csv_p.read_text(encoding="utf-8-sig")
            current_parts.append({"text": f"[数据库查询结果CSV]\n{csv_text}\n\n"})

    current_parts.append({"text": user_input})
    contents.append({"role": "user", "parts": current_parts})

    return contents


def _extract_images_from_response(response, project_dir: Path) -> list[str]:
    """
    从 Gemini 响应中提取内联图片，保存到项目目录，返回图片路径列表。
    参考：https://ai.google.dev/gemini-api/docs/code-execution
    """
    saved_paths = []
    for candidate in response.candidates:
        for part in candidate.content.parts:
            # 代码执行输出的内联图片
            if hasattr(part, "inline_data") and part.inline_data:
                mime = part.inline_data.mime_type
                ext = "png" if "png" in mime else "jpg" if "jpeg" in mime else "bin"
                filename = f"{_snowflake_id()}.{ext}"
                img_path = project_dir / filename
                with open(img_path, "wb") as f:
                    f.write(base64.b64decode(part.inline_data.data))
                saved_paths.append(str(img_path))
    return saved_paths


def _extract_text_from_response(response) -> str:
    """提取响应中所有文本部分"""
    texts = []
    for candidate in response.candidates:
        for part in candidate.content.parts:
            if hasattr(part, "text") and part.text:
                texts.append(part.text)
    return "\n".join(texts)


# ==================== 核心流程 ====================
def run_chart_generation(
    user_input: str,
    context: list,
    project_dir: Path,
    jsonl_path: Path,
    csv_path: str = None
) -> dict:
    """
    调用 Gemini（启用代码执行工具）生成图表。

    参数：
      user_input   - 当前用户输入文本
      context      - 多轮对话上下文列表
      project_dir  - 项目目录（用于保存图片）
      jsonl_path   - jsonl 记录文件路径
      csv_path     - 可选，SQL 查询结果 CSV 路径

    返回：
      {
        "reply_text": str,          # AI 回复文本
        "image_paths": list[str],   # 生成的图片路径列表（可能为空）
        "error": str | None
      }
    """
    _append_jsonl(jsonl_path, {
        "timestamp": datetime.now().isoformat(),
        "status": "生成图表",
        "csv_path": csv_path
    })

    system_instruction = (
        "你是一个专业的数据分析和图表绘制助手。\n"
        "当用户提供数据时，请使用 Python Matplotlib 生成美观的图表。\n"
        "图表要求：\n"
        "- 使用中文标题和标签（设置 matplotlib 中文字体：plt.rcParams['font.sans-serif'] = ['SimHei', 'DejaVu Sans']）\n"
        "- 配色美观，添加网格线\n"
        "- 图表尺寸适中（figsize=(10, 6) 或根据内容调整）\n"
        "- 如果问题是纯问答，无需生成图表，直接回答即可。\n"
        "- 生成图表后使用 plt.show() 展示（代码执行环境会自动捕获图片）。"
    )

    # 配置代码执行工具（Gemini SDK 写法）
    model_with_si = genai.GenerativeModel(
        model_name="gemini-2.5-pro-preview-03-25",
        tools="code_execution",
        system_instruction=system_instruction
    )

    contents = _build_contents(user_input, context, csv_path)

    try:
        response = model_with_si.generate_content(contents)
    except Exception as e:
        _append_jsonl(jsonl_path, {
            "timestamp": datetime.now().isoformat(),
            "status": "图表生成失败",
            "error": str(e)
        })
        return {
            "reply_text": f"图表生成失败：{e}",
            "image_paths": [],
            "error": str(e)
        }

    # 提取文本和图片
    reply_text = _extract_text_from_response(response)
    image_paths = _extract_images_from_response(response, project_dir)

    _append_jsonl(jsonl_path, {
        "timestamp": datetime.now().isoformat(),
        "status": "图表生成完成",
        "image_paths": image_paths,
        "has_text_reply": bool(reply_text)
    })

    return {
        "reply_text": reply_text,
        "image_paths": image_paths,
        "error": None
    }
