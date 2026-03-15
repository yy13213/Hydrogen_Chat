"""
main.py — Deep Research 服务入口
端口：3031
提供：
- POST /research                  启动深度研究，返回 project_dir（时间戳）
- GET  /progress/{project_dir}    轮询研究进展
- GET  /report/{project_dir}      获取最终报告
- GET  /detail/{project_dir}      获取详细数据（思维导图、质疑等）
- GET  /logs/{project_dir}        获取项目日志
- GET  /projects                  列出所有历史研究项目
"""

import asyncio
import glob
import json
import os
import sys
from contextlib import asynccontextmanager
from datetime import datetime

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

# 确保 deep_research 目录在 sys.path 中（从任意工作目录启动均可）
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

load_dotenv(os.path.join(_HERE, ".env"))

from gemini_client import PROJECTS_DIR
from logger import get_framework_logger, get_project_logger

PORT = int(os.getenv("DEEP_RESEARCH_PORT", 3031))

# 运行中的研究任务状态（内存缓存）
_running_tasks: dict[str, dict] = {}

fw_log = get_framework_logger("main")


# ==================== 请求/响应模型 ====================

class ResearchRequest(BaseModel):
    question: str


class ResearchResponse(BaseModel):
    project_dir: str
    message: str


# ==================== 研究任务执行 ====================

async def _run_research(project_dir: str, question: str) -> None:
    """在后台执行完整深度研究流程"""
    log = get_project_logger(project_dir, "main")
    _running_tasks[project_dir] = {
        "status": "running",
        "start_time": datetime.now().isoformat(),
        "question": question,
        "current_stage": "Planner",
        "error": None,
    }
    log.info(f"研究任务启动，问题：{question[:80]}...")
    try:
        from Planner import Planner
        planner = Planner(project_dir)
        await planner.init_research(question)
        _running_tasks[project_dir]["status"] = "completed"
        _running_tasks[project_dir]["end_time"] = datetime.now().isoformat()
        log.info("研究任务全部完成")
    except Exception as e:
        _running_tasks[project_dir]["status"] = "failed"
        _running_tasks[project_dir]["error"] = str(e)
        _running_tasks[project_dir]["end_time"] = datetime.now().isoformat()
        log.error(f"研究任务失败: {e}", exc_info=True)


# ==================== FastAPI 应用 ====================

@asynccontextmanager
async def lifespan(app: FastAPI):
    os.makedirs(PROJECTS_DIR, exist_ok=True)
    fw_log.info("=" * 50)
    fw_log.info(f"Deep Research 服务启动，端口: {PORT}")
    fw_log.info(f"项目目录: {PROJECTS_DIR}")
    fw_log.info("=" * 50)
    print(f"\n{'='*60}")
    print(f"🔬 Deep Research 服务启动")
    print(f"📡 端口: {PORT}")
    print(f"📁 项目目录: {PROJECTS_DIR}")
    print(f"{'='*60}\n")
    yield
    fw_log.info("Deep Research 服务关闭")


app = FastAPI(
    title="Deep Research API",
    description="深度研究框架服务",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/research", response_model=ResearchResponse)
async def start_research(request: ResearchRequest, background_tasks: BackgroundTasks):
    """启动深度研究，返回项目目录名（时间戳），用于后续轮询"""
    if not request.question.strip():
        raise HTTPException(status_code=400, detail="研究问题不能为空")

    from Planner import Planner
    planner = Planner.create_project()
    project_dir = planner.project_dir

    fw_log.info(f"新研究任务创建：{project_dir}，问题：{request.question[:80]}...")
    background_tasks.add_task(_run_research, project_dir, request.question)

    return ResearchResponse(
        project_dir=project_dir,
        message=f"研究已启动，项目ID：{project_dir}",
    )


@app.get("/progress/{project_dir}")
async def get_progress(project_dir: str):
    """轮询研究进展"""
    base = os.path.join(PROJECTS_DIR, project_dir)
    if not os.path.exists(base):
        raise HTTPException(status_code=404, detail="项目不存在")

    task_info = _running_tasks.get(project_dir, {})
    researcher_list = _read_jsonl_safe(os.path.join(base, "Researcher_list.jsonl"))

    total = len(researcher_list)
    completed = sum(1 for r in researcher_list if r.get("status") == "completed")
    running = sum(1 for r in researcher_list if r.get("status") == "running")

    report_files = glob.glob(os.path.join(base, "*.md"))
    has_report = len(report_files) > 0
    report_name = os.path.basename(report_files[0]) if report_files else None

    doubts = _read_jsonl_safe(os.path.join(base, "doubt.jsonl"))

    return {
        "project_dir": project_dir,
        "status": task_info.get("status", "unknown"),
        "current_stage": task_info.get("current_stage", ""),
        "start_time": task_info.get("start_time"),
        "end_time": task_info.get("end_time"),
        "error": task_info.get("error"),
        "progress": {
            "total_sub_researches": total,
            "completed": completed,
            "running": running,
            "pending": total - completed - running,
        },
        "researcher_list": researcher_list,
        "doubts_count": len(doubts),
        "has_report": has_report,
        "report_name": report_name,
    }


@app.get("/report/{project_dir}")
async def get_report(project_dir: str):
    """获取最终研究报告（Markdown 格式）"""
    base = os.path.join(PROJECTS_DIR, project_dir)
    if not os.path.exists(base):
        raise HTTPException(status_code=404, detail="项目不存在")

    report_files = glob.glob(os.path.join(base, "*.md"))
    if not report_files:
        raise HTTPException(status_code=404, detail="报告尚未生成")

    with open(report_files[0], "r", encoding="utf-8") as f:
        content = f.read()

    return PlainTextResponse(content=content, media_type="text/markdown; charset=utf-8")


@app.get("/detail/{project_dir}")
async def get_detail(project_dir: str):
    """获取项目详细数据（思维导图、质疑记录等）"""
    base = os.path.join(PROJECTS_DIR, project_dir)
    if not os.path.exists(base):
        raise HTTPException(status_code=404, detail="项目不存在")

    researcher_list = _read_jsonl_safe(os.path.join(base, "Researcher_list.jsonl"))
    doubts = _read_jsonl_safe(os.path.join(base, "doubt.jsonl"))
    shared_memory = _read_jsonl_safe(os.path.join(base, "shared_memory.jsonl"))

    researcher_tasks = {}
    for i in range(1, 6):
        r_id = f"Researcher{i}"
        tasks = _read_jsonl_safe(os.path.join(base, r_id, "task_list.jsonl"))
        if tasks:
            researcher_tasks[r_id] = tasks

    return {
        "project_dir": project_dir,
        "researcher_list": researcher_list,
        "researcher_tasks": researcher_tasks,
        "doubts": doubts,
        "shared_memory_summary": {
            "total_records": len(shared_memory),
            "sub_researches": [
                {
                    "sub_research_id": r.get("sub_research_id"),
                    "researcher_id": r.get("researcher_id"),
                    "goal": r.get("goal"),
                    "task_count": len(r.get("tasks", [])),
                }
                for r in shared_memory
                if r.get("type") == "sub_research"
            ],
        },
    }


@app.get("/logs/{project_dir}")
async def get_logs(project_dir: str, log_type: str = "research", lines: int = 200):
    """
    获取项目日志
    - log_type: research（默认）| error
    - lines: 返回最后 N 行，默认200
    """
    base = os.path.join(PROJECTS_DIR, project_dir)
    if not os.path.exists(base):
        raise HTTPException(status_code=404, detail="项目不存在")

    log_file = os.path.join(base, "logs", f"{log_type}.log")
    if not os.path.exists(log_file):
        return PlainTextResponse(content="（暂无日志）", media_type="text/plain; charset=utf-8")

    with open(log_file, "r", encoding="utf-8") as f:
        all_lines = f.readlines()

    tail = "".join(all_lines[-lines:])
    return PlainTextResponse(content=tail, media_type="text/plain; charset=utf-8")


@app.get("/logs")
async def get_framework_logs(lines: int = 200):
    """获取框架级日志"""
    log_file = os.path.join(PROJECTS_DIR, "logs", "framework.log")
    if not os.path.exists(log_file):
        return PlainTextResponse(content="（暂无框架日志）", media_type="text/plain; charset=utf-8")

    with open(log_file, "r", encoding="utf-8") as f:
        all_lines = f.readlines()

    tail = "".join(all_lines[-lines:])
    return PlainTextResponse(content=tail, media_type="text/plain; charset=utf-8")


@app.get("/projects")
async def list_projects():
    """列出所有历史研究项目"""
    if not os.path.exists(PROJECTS_DIR):
        return {"projects": []}

    projects = []
    for entry in sorted(os.scandir(PROJECTS_DIR), key=lambda e: e.name, reverse=True):
        # 跳过 logs 目录
        if not entry.is_dir() or entry.name == "logs":
            continue

        base = entry.path
        researcher_list = _read_jsonl_safe(os.path.join(base, "Researcher_list.jsonl"))
        report_files = glob.glob(os.path.join(base, "*.md"))

        shared = _read_jsonl_safe(os.path.join(base, "shared_memory.jsonl"))
        question = next((r.get("user_question", "") for r in shared if r.get("type") == "init"), "")

        task_info = _running_tasks.get(entry.name, {})

        projects.append({
            "project_dir": entry.name,
            "question": question,
            "status": task_info.get("status", "completed" if report_files else "unknown"),
            "start_time": task_info.get("start_time", entry.name),
            "has_report": len(report_files) > 0,
            "report_name": os.path.basename(report_files[0]) if report_files else None,
            "sub_research_count": len(researcher_list),
        })

    return {"projects": projects}


def _read_jsonl_safe(path: str) -> list:
    if not os.path.exists(path):
        return []
    records = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    except Exception:
        pass
    return records


if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=PORT,
        log_level="info",
    )
