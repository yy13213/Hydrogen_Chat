"""
SQL 生成与执行模块
负责：
1. 使用 Gemini 视觉模型根据用户输入和 ER 图生成 SQL
2. 执行 SQL 并保存结果为 CSV
3. 验证查询结果是否完整，失败则循环重试（最多 8 次）
"""

import os
import csv
import json
import base64
import importlib.util
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
from google import genai
from google.genai import types
import psycopg2

# 加载 .env 文件中的环境变量
load_dotenv()

# 用 importlib 加载数据库模块
_db_module_path = Path(__file__).parent / "database_query.py"
_spec = importlib.util.spec_from_file_location("database_query", _db_module_path)
_db_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_db_module)
DB_CONFIG = _db_module.DB_CONFIG

# ==================== Gemini 客户端配置 ====================
GEMINI_BASE_URL = "http://localhost:6773"
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "placeholder")

client = genai.Client(
    api_key=GEMINI_API_KEY,
    http_options=types.HttpOptions(base_url=GEMINI_BASE_URL)
)

ER_CHART_PATH = Path(__file__).parent / "ER_chart.jpg"

MAX_RETRY = 8


# ==================== 工具函数 ====================
def _load_er_image() -> types.Part:
    """加载 ER 图为 Gemini Part 格式"""
    with open(ER_CHART_PATH, "rb") as f:
        data = f.read()
    return types.Part.from_bytes(data=data, mime_type="image/jpeg")


def _append_jsonl(jsonl_path: Path, record: dict):
    """追加一条 JSON 记录到 jsonl 文件"""
    with open(jsonl_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _build_context_parts(context: list) -> list:
    """
    将多轮上下文转换为新版 SDK types.Part 列表。
    context 每项格式：
      {"role": "user"/"model", "text": "...", "files": [...], "csv": "..."}
    """
    parts = []
    for item in context:
        if item.get("text"):
            parts.append(types.Part.from_text(text=item["text"]))
        for file_path in item.get("files", []):
            p = Path(file_path)
            if p.exists():
                suffix = p.suffix.lower()
                mime = "image/jpeg" if suffix in (".jpg", ".jpeg") else \
                       "image/png" if suffix == ".png" else \
                       "text/plain"
                with open(p, "rb") as f:
                    raw = f.read()
                if mime.startswith("image"):
                    parts.append(types.Part.from_bytes(data=raw, mime_type=mime))
                else:
                    parts.append(types.Part.from_text(text=raw.decode("utf-8", errors="replace")))
        if item.get("csv"):
            csv_path = Path(item["csv"])
            if csv_path.exists():
                parts.append(types.Part.from_text(
                    text=f"[CSV数据]\n{csv_path.read_text(encoding='utf-8')}"
                ))
    return parts


def _execute_sql_safe(sql: str):
    """
    执行 SQL，返回 (columns, rows, error_msg)。
    columns: list[str] | None
    rows: list[tuple] | None
    error_msg: str | None
    """
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


def _save_csv(project_dir: Path, columns: list, rows: list) -> Path:
    """将查询结果保存为 data.csv，返回文件路径"""
    csv_path = project_dir / "data.csv"
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(columns)
        writer.writerows(rows)
    return csv_path


# ==================== 核心流程 ====================
def run_sql_generation(
    user_input: str,
    context: list,
    project_dir: Path,
    jsonl_path: Path
) -> dict:
    """
    SQL 生成 → 执行 → 验证 循环，最多重试 MAX_RETRY 次。

    返回：
      {
        "success": bool,
        "csv_path": str | None,
        "error": str | None,
        "sql": str | None
      }
    """
    er_image = _load_er_image()
    context_parts = _build_context_parts(context)
    last_error = None
    last_sql = None

    # 结构化输出 schema
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

    for attempt in range(1, MAX_RETRY + 1):
        # ---------- 1. 生成 SQL ----------
        status = "生成SQL" if attempt == 1 else "重新生成SQL"
        _append_jsonl(jsonl_path, {
            "timestamp": datetime.now().isoformat(),
            "status": status,
            "attempt": attempt
        })

        prompt_parts = []
        prompt_parts.extend(context_parts)
        prompt_parts.append(er_image)
        prompt_text = (
            f"用户问题：{user_input}\n\n"
            "请根据上方数据库 ER 图，生成一条 PostgreSQL SQL 查询语句来回答用户问题。\n"
            "只返回 SQL 语句本身，不要包含 markdown 代码块标记。"
        )
        if last_error:
            prompt_text += (
                f"\n\n上次执行的 SQL：\n{last_sql}\n"
                f"执行报错：{last_error}\n"
                "请修正 SQL 语句。"
            )
        prompt_parts.append(types.Part.from_text(text=prompt_text))

        try:
            sql_response = client.models.generate_content(
                model="gemini-3.1-pro-preview",
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
            _append_jsonl(jsonl_path, {
                "timestamp": datetime.now().isoformat(),
                "status": "生成SQL失败",
                "attempt": attempt,
                "error": str(e)
            })
            last_error = str(e)
            continue

        _append_jsonl(jsonl_path, {
            "timestamp": datetime.now().isoformat(),
            "status": "SQL已生成",
            "attempt": attempt,
            "sql": generated_sql
        })

        # ---------- 2. 执行 SQL ----------
        _append_jsonl(jsonl_path, {
            "timestamp": datetime.now().isoformat(),
            "status": "执行SQL",
            "attempt": attempt,
            "sql": generated_sql
        })

        columns, rows, exec_error = _execute_sql_safe(generated_sql)

        if exec_error:
            last_error = exec_error
            _append_jsonl(jsonl_path, {
                "timestamp": datetime.now().isoformat(),
                "status": "执行SQL失败",
                "attempt": attempt,
                "error": exec_error
            })
            continue

        # 保存 CSV
        csv_path = _save_csv(project_dir, columns, rows)
        csv_content = csv_path.read_text(encoding="utf-8-sig")

        _append_jsonl(jsonl_path, {
            "timestamp": datetime.now().isoformat(),
            "status": "SQL执行成功",
            "attempt": attempt,
            "csv_path": str(csv_path),
            "row_count": len(rows)
        })

        # ---------- 3. 验证结果完整性 ----------
        _append_jsonl(jsonl_path, {
            "timestamp": datetime.now().isoformat(),
            "status": "验证查询结果",
            "attempt": attempt
        })

        validation_parts = []
        validation_parts.extend(context_parts)
        validation_parts.append(er_image)
        validation_parts.append(types.Part.from_text(
            text=(
                f"用户问题：{user_input}\n\n"
                f"执行的 SQL：\n{generated_sql}\n\n"
                f"查询结果（CSV）：\n{csv_content}\n\n"
                "请判断查询结果是否完整地回答了用户问题。"
                "如果完整返回 true，否则返回 false 并说明原因。"
            )
        ))

        try:
            val_response = client.models.generate_content(
                model="gemini-3.1-pro-preview",
                contents=validation_parts,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=val_schema,
                )
            )
            val_result = json.loads(val_response.text)
            is_success = val_result.get("success", False)
            reason = val_result.get("reason", "")
        except Exception as e:
            _append_jsonl(jsonl_path, {
                "timestamp": datetime.now().isoformat(),
                "status": "验证失败",
                "attempt": attempt,
                "error": str(e)
            })
            last_error = str(e)
            continue

        _append_jsonl(jsonl_path, {
            "timestamp": datetime.now().isoformat(),
            "status": "验证完成",
            "attempt": attempt,
            "success": is_success,
            "reason": reason
        })

        if is_success:
            return {
                "success": True,
                "csv_path": str(csv_path),
                "error": None,
                "sql": generated_sql
            }
        else:
            last_error = f"结果不完整：{reason}"

    # 超过最大重试次数
    _append_jsonl(jsonl_path, {
        "timestamp": datetime.now().isoformat(),
        "status": "SQL生成超出最大重试次数",
        "max_retry": MAX_RETRY
    })
    return {
        "success": False,
        "csv_path": None,
        "error": last_error or "超出最大重试次数",
        "sql": last_sql
    }
