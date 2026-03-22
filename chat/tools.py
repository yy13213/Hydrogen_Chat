import httpx
import json
import csv
import io
import os
import importlib.util
import psycopg2
from pathlib import Path
from dotenv import load_dotenv
from tavily import TavilyClient
from google import genai
from google.genai import types

load_dotenv(Path(__file__).parent / ".env")

# ── 知识库配置 ─────────────────────────────────────────────
KNOWLEDGE_BASE_URL = os.getenv("KNOWLEDGE_BASE_URL", "http://localhost:6772/triggers/webhook-debug/mnwXLKRp0WDOeH7XjBjAAOO6")

# ── Tavily 网络搜索 ────────────────────────────────────────
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")

# ── 数据库配置（复用 chart_agent/database_query.py） ────────
_db_module_path = Path(__file__).parent.parent / "chart_agent" / "database_query.py"
_spec = importlib.util.spec_from_file_location("database_query", _db_module_path)
_db_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_db_module)
DB_CONFIG = _db_module.DB_CONFIG

# ── ER 图路径（与 sql_generation.py 相同） ──────────────────
ER_CHART_PATH = Path(__file__).parent.parent / "chart_agent" / "ER_chart.jpg"

# ── Gemini 客户端配置 ────────────────────────────────────────
GEMINI_BASE_URL = os.getenv("GEMINI_BASE_URL", "http://localhost:6773")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "placeholder")
SQL_MODEL = os.getenv("SQL_MODEL", "gemini-3.1-pro-preview-customtools")
MAX_RETRY = 5


def _get_client() -> genai.Client:
    return genai.Client(
        api_key=GEMINI_API_KEY,
        http_options=types.HttpOptions(base_url=GEMINI_BASE_URL)
    )


def _load_er_image() -> types.Part | None:
    """加载 ER 图，不存在则返回 None"""
    if not ER_CHART_PATH.exists():
        return None
    with open(ER_CHART_PATH, "rb") as f:
        data = f.read()
    return types.Part.from_bytes(data=data, mime_type="image/jpeg")


def _execute_sql_safe(sql: str):
    """执行 SQL，返回 (columns, rows, error_msg)"""
    connection = None
    try:
        connection = psycopg2.connect(**DB_CONFIG)
        with connection.cursor() as cursor:
            cursor.execute(sql)
            columns = [desc[0] for desc in cursor.description] if cursor.description else []
            rows = cursor.fetchall()
            connection.commit()
            return columns, rows, None
    except Exception as e:
        return None, None, str(e)
    finally:
        if connection:
            connection.close()


def _rows_to_text(columns: list, rows: list) -> str:
    """将查询结果转为 CSV 格式文本字符串"""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(columns)
    writer.writerows(rows)
    return buf.getvalue()


def search_web(query: str) -> str:
    """使用 Tavily 搜索网络，返回格式化的搜索结果"""
    try:
        tavily_client = TavilyClient(api_key=TAVILY_API_KEY)
        response = tavily_client.search(query)
        results = response.get("results", [])
        if not results:
            return "未找到相关搜索结果。"
        lines = []
        for i, r in enumerate(results[:5], 1):
            title = r.get("title", "无标题")
            url = r.get("url", "")
            content = r.get("content", "").strip()[:300]
            lines.append(f"**{i}. {title}**\n{url}\n{content}")
        return "\n\n".join(lines)
    except Exception as e:
        return f"网络搜索失败: {str(e)}"


def query_knowledge_base(question: str) -> str:
    """查询知识库，返回相关内容"""
    try:
        response = httpx.post(
            KNOWLEDGE_BASE_URL,
            json={"question": question},
            timeout=30.0
        )
        response.raise_for_status()
        data = response.json()
        if isinstance(data, dict):
            return data.get("answer") or data.get("result") or data.get("text") or str(data)
        return str(data)
    except httpx.TimeoutException:
        return "知识库查询超时，请稍后重试。"
    except httpx.HTTPStatusError as e:
        return f"知识库服务返回错误: {e.response.status_code}"
    except Exception as e:
        return f"知识库查询失败: {str(e)}"


def query_database(user_question: str) -> str:
    """
    仿照 sql_generation.py 的逻辑：
    1. 用 Gemini 根据 ER 图和用户问题生成 SQL
    2. 执行 SQL
    3. 验证结果完整性，失败则重试
    最终返回 CSV 格式的文本内容（而非文件路径）
    """
    client = _get_client()
    er_image = _load_er_image()

    sql_schema = types.Schema(
        type=types.Type.OBJECT,
        properties={
            "sql": types.Schema(type=types.Type.STRING),
            "explanation": types.Schema(type=types.Type.STRING),
        },
        required=["sql", "explanation"]
    )
    val_schema = types.Schema(
        type=types.Type.OBJECT,
        properties={
            "success": types.Schema(type=types.Type.BOOLEAN),
            "reason": types.Schema(type=types.Type.STRING),
        },
        required=["success", "reason"]
    )

    last_error = None
    last_sql = None

    for attempt in range(1, MAX_RETRY + 1):
        # ── 1. 生成 SQL ────────────────────────────────────
        prompt_text = (
            f"用户问题：{user_question}\n\n"
            "请根据上方数据库 ER 图，生成一条 PostgreSQL SQL 查询语句来回答用户问题。\n"
            "只返回 SQL 语句本身，不要包含 markdown 代码块标记。"
        )
        if last_error:
            prompt_text += (
                f"\n\n上次执行的 SQL：\n{last_sql}\n"
                f"执行报错：{last_error}\n"
                "请修正 SQL 语句。"
            )

        prompt_parts = []
        if er_image:
            prompt_parts.append(er_image)
        prompt_parts.append(types.Part.from_text(text=prompt_text))

        try:
            sql_response = client.models.generate_content(
                model=SQL_MODEL,
                contents=prompt_parts,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=sql_schema,
                )
            )
            sql_result = json.loads(sql_response.text)
            generated_sql = sql_result["sql"].strip()
            last_sql = generated_sql
        except Exception as e:
            last_error = str(e)
            continue

        # ── 2. 执行 SQL ────────────────────────────────────
        columns, rows, exec_error = _execute_sql_safe(generated_sql)

        if exec_error:
            last_error = exec_error
            continue

        csv_text = _rows_to_text(columns, rows)

        # ── 3. 验证结果完整性 ──────────────────────────────
        val_parts = []
        if er_image:
            val_parts.append(er_image)
        val_parts.append(types.Part.from_text(
            text=(
                f"用户问题：{user_question}\n\n"
                f"执行的 SQL：\n{generated_sql}\n\n"
                f"查询结果（CSV）：\n{csv_text}\n\n"
                "请判断查询结果所包含的数据是否能完整地回答用户问题。"
                "如果完整返回 true，否则返回 false 并说明原因。"
            )
        ))

        try:
            val_response = client.models.generate_content(
                model=SQL_MODEL,
                contents=val_parts,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=val_schema,
                )
            )
            val_result = json.loads(val_response.text)
            is_success = val_result.get("success", False)
            reason = val_result.get("reason", "")
        except Exception as e:
            last_error = str(e)
            continue

        if is_success:
            return (
                f"**数据库查询成功**（SQL：`{generated_sql}`）\n\n"
                f"```csv\n{csv_text}```"
            )
        else:
            last_error = f"结果不完整：{reason}"

    return (
        f"❌ 数据库查询失败，已重试 {MAX_RETRY} 次。\n"
        f"最后执行的 SQL：`{last_sql}`\n"
        f"最后错误：{last_error}"
    )
