"""
智能图表绘制服务 - 主入口
端口：9621

流程：
1. 接受用户问题（含可选图片/文件）
2. 创建以时间戳命名的项目目录，建立 jsonl 记录文件
3. 用 Gemini Flash Lite（视觉）判断是否需要查询数据库
4. 需要 → sql_generation.py → chart_generation.py
   不需要 → chart_generation.py
5. 支持多轮对话（通过 project_name 关联上下文）
"""

import os
import json
import uuid
from pathlib import Path
from datetime import datetime
from typing import List, Optional

from google import genai
from google.genai import types
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn
from dotenv import load_dotenv
from sql_generation import run_sql_generation
from chart_generation import run_chart_generation

# 加载 .env 文件中的环境变量
load_dotenv()

# ==================== Gemini 客户端配置 ====================
GEMINI_BASE_URL = "http://localhost:6773"
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "placeholder")

client = genai.Client(
    api_key=GEMINI_API_KEY,
    http_options=types.HttpOptions(base_url=GEMINI_BASE_URL)
)

# ==================== 路径配置 ====================
BASE_DIR = Path(__file__).parent
PROJECTS_DIR = BASE_DIR / "projects"
ER_CHART_PATH = BASE_DIR / "ER_chart.jpg"
PROJECTS_DIR.mkdir(exist_ok=True)

# ==================== FastAPI 应用 ====================
app = FastAPI(title="智能图表绘制服务", version="1.0.2")

# 挂载静态文件目录，供前端访问生成的图片
app.mount("/projects", StaticFiles(directory=str(PROJECTS_DIR)), name="projects")


# ==================== 工具函数 ====================
def _snowflake_id() -> str:
    return uuid.uuid4().hex


def _append_jsonl(jsonl_path: Path, record: dict):
    with open(jsonl_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _read_jsonl(jsonl_path: Path) -> list:
    records = []
    if jsonl_path.exists():
        for line in jsonl_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return records


def _load_er_image_inline() -> types.Part:
    with open(ER_CHART_PATH, "rb") as f:
        data = f.read()
    return types.Part.from_bytes(data=data, mime_type="image/jpeg")


def _save_uploaded_file(upload: UploadFile, project_dir: Path) -> str:
    """将上传文件以雪花ID重命名保存，返回保存路径"""
    suffix = Path(upload.filename).suffix if upload.filename else ""
    filename = f"{_snowflake_id()}{suffix}"
    dest = project_dir / filename
    with open(dest, "wb") as f:
        f.write(upload.file.read())
    return str(dest)


def _build_context_from_jsonl(jsonl_path: Path) -> list:
    """
    从 jsonl 中重建多轮对话上下文列表。
    每条 user_turn / model_turn 记录构成一轮对话。
    """
    records = _read_jsonl(jsonl_path)
    context = []
    for r in records:
        if r.get("status") in ("user_turn", "model_turn"):
            context.append({
                "role": "user" if r["status"] == "user_turn" else "model",
                "text": r.get("text", ""),
                "files": r.get("files", []),
                "csv": r.get("csv", "")
            })
    return context


def _check_need_db(user_input: str, uploaded_file_paths: list, context: list) -> bool:
    """
    使用 gemini-3.1-pro-preview（视觉）判断是否需要查询数据库。
    结构化返回 {"need_db": true/false, "reason": "..."}
    """
    er_image = _load_er_image_inline()
    parts = []

    # 历史上下文文本摘要
    if context:
        history_text = "\n".join(
            f"[{'用户' if c['role'] == 'user' else 'AI'}]: {c['text']}"
            for c in context if c.get("text")
        )
        parts.append(types.Part.from_text(text=f"[历史对话]\n{history_text}\n\n"))

    # 用户上传的图片/文件
    for fp in uploaded_file_paths:
        p = Path(fp)
        if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".gif", ".webp"):
            with open(p, "rb") as f:
                raw = f.read()
            mime = "image/jpeg" if p.suffix.lower() in (".jpg", ".jpeg") else "image/png"
            parts.append(types.Part.from_bytes(data=raw, mime_type=mime))
        elif p.suffix.lower() in (".txt", ".csv"):
            parts.append(types.Part.from_text(
                text=p.read_text(encoding="utf-8", errors="replace")
            ))

    # ER 图
    parts.append(er_image)

    parts.append(types.Part.from_text(
        text=(
            f"用户问题：{user_input}\n\n"
            "根据上方数据库 ER 图和用户问题，判断是否需要查询数据库才能回答。\n"
            "如果用户问题需要具体数据（如统计、列表、趋势等），返回 need_db=true；\n"
            "如果是纯问答、解释或不需要数据库数据，返回 need_db=false。"
        )
    ))

    need_db_schema = types.Schema(
        type=types.Type.OBJECT,
        properties={
            "need_db": types.Schema(type=types.Type.BOOLEAN),
            "reason": types.Schema(type=types.Type.STRING),
        },
        required=["need_db", "reason"]
    )

    try:
        response = client.models.generate_content(
            model="gemini-3.1-pro-preview",
            contents=parts,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=need_db_schema,
            )
        )
        result = json.loads(response.text)
        return result.get("need_db", False)
    except Exception:
        return False


# ==================== API 路由 ====================

@app.post("/chat")
async def chat(
    user_input: str = Form(..., description="用户问题文本"),
    project_name: Optional[str] = Form(None, description="项目名（多轮对话时传入，首次为空）"),
    files: Optional[List[UploadFile]] = File(None, description="上传的图片或文件（可选）")
):
    """
    主对话接口。
    - 首次调用不传 project_name，服务端创建新项目并返回 project_name。
    - 后续多轮对话传入 project_name 以延续上下文。
    """
    # ---------- 1. 初始化/加载项目 ----------
    if not project_name:
        # 新项目：以时间戳命名
        project_name = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        project_dir = PROJECTS_DIR / project_name
        project_dir.mkdir(parents=True, exist_ok=True)
        is_new = True
    else:
        project_dir = PROJECTS_DIR / project_name
        if not project_dir.exists():
            raise HTTPException(status_code=404, detail=f"项目 {project_name} 不存在")
        is_new = False

    jsonl_path = project_dir / "session.jsonl"

    # ---------- 2. 保存上传文件 ----------
    uploaded_file_paths = []
    if files:
        for upload in files:
            if upload.filename:
                saved_path = _save_uploaded_file(upload, project_dir)
                uploaded_file_paths.append(saved_path)

    # ---------- 3. 记录用户输入到 jsonl ----------
    _append_jsonl(jsonl_path, {
        "timestamp": datetime.now().isoformat(),
        "status": "user_turn",
        "text": user_input,
        "files": uploaded_file_paths,
        "csv": ""
    })

    # ---------- 4. 加载历史上下文（不含本轮） ----------
    all_records = _read_jsonl(jsonl_path)
    context = []
    for r in all_records[:-1]:  # 排除刚写入的本轮
        if r.get("status") in ("user_turn", "model_turn"):
            context.append({
                "role": "user" if r["status"] == "user_turn" else "model",
                "text": r.get("text", ""),
                "files": r.get("files", []),
                "csv": r.get("csv", "")
            })

    # ---------- 5. 判断是否需要查询数据库 ----------
    _append_jsonl(jsonl_path, {
        "timestamp": datetime.now().isoformat(),
        "status": "正在思考",
        "message": "判断是否需要查询数据库"
    })

    need_db = _check_need_db(user_input, uploaded_file_paths, context)

    _append_jsonl(jsonl_path, {
        "timestamp": datetime.now().isoformat(),
        "status": "思考完成",
        "need_db": need_db
    })

    # ---------- 6. 执行流程 ----------
    csv_path = None

    if need_db:
        # 6a. SQL 生成与执行
        sql_result = run_sql_generation(
            user_input=user_input,
            context=context,
            project_dir=project_dir,
            jsonl_path=jsonl_path
        )
        if sql_result["success"]:
            csv_path = sql_result["csv_path"]
        else:
            # SQL 失败也继续尝试图表生成（可能是问答型）
            _append_jsonl(jsonl_path, {
                "timestamp": datetime.now().isoformat(),
                "status": "SQL流程失败，降级为直接回答",
                "error": sql_result.get("error")
            })

    # 6b. 图表生成
    chart_result = run_chart_generation(
        user_input=user_input,
        context=context,
        project_dir=project_dir,
        jsonl_path=jsonl_path,
        csv_path=csv_path
    )

    # ---------- 7. 记录 AI 回复到 jsonl ----------
    _append_jsonl(jsonl_path, {
        "timestamp": datetime.now().isoformat(),
        "status": "model_turn",
        "text": chart_result["reply_text"],
        "files": chart_result["image_paths"],
        "csv": csv_path or ""
    })

    # ---------- 8. 构建响应 ----------
    # 将绝对路径转为可访问的 URL 路径
    image_urls = []
    for img_path in chart_result["image_paths"]:
        rel = Path(img_path).relative_to(PROJECTS_DIR)
        image_urls.append(f"/projects/{rel.as_posix()}")

    return JSONResponse({
        "project_name": project_name,
        "reply_text": chart_result["reply_text"],
        "image_urls": image_urls,
        "need_db": need_db,
        "csv_path": csv_path,
        "error": chart_result.get("error")
    })


@app.get("/progress/{project_name}")
async def get_progress(project_name: str):
    """
    轮询接口：返回指定项目的 jsonl 执行记录，供前端展示进度。
    """
    project_dir = PROJECTS_DIR / project_name
    jsonl_path = project_dir / "session.jsonl"

    if not jsonl_path.exists():
        raise HTTPException(status_code=404, detail="项目不存在或尚未创建记录")

    records = _read_jsonl(jsonl_path)
    return JSONResponse({"project_name": project_name, "records": records})


@app.get("/health")
async def health():
    return {"status": "ok", "service": "智能图表绘制服务", "port": 9621}


# ==================== 启动 ====================
if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=9621,
        reload=False,
        log_level="info"
    )
