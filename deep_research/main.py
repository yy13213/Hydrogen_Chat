"""
main.py — Deep Research 服务入口
端口：3031
提供：
- POST /research         启动深度研究，返回 project_dir（时间戳）
- GET  /progress/{project_dir}  轮询研究进展
- GET  /report/{project_dir}    获取最终报告
- GET  /projects         列出所有历史研究项目
"""

import asyncio
import glob
import json
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel

load_dotenv()

PROJECTS_DIR = os.getenv("PROJECTS_DIR", "projects")
PORT = int(os.getenv("DEEP_RESEARCH_PORT", 3031))

# 运行中的研究任务状态
_running_tasks: dict[str, dict] = {}


# ==================== 请求/响应模型 ====================

class ResearchRequest(BaseModel):
    question: str


class ResearchResponse(BaseModel):
    project_dir: str
    message: str


# ==================== 研究任务执行 ====================

async def _run_research(project_dir: str, question: str) -> None:
    """在后台执行完整深度研究流程"""
    _running_tasks[project_dir] = {
        "status": "running",
        "start_time": datetime.now().isoformat(),
        "question": question,
        "current_stage": "Planner",
        "error": None,
    }
    try:
        from Planner import Planner
        planner = Planner(project_dir)
        await planner.init_research(question)
        _running_tasks[project_dir]["status"] = "completed"
        _running_tasks[project_dir]["end_time"] = datetime.now().isoformat()
    except Exception as e:
        _running_tasks[project_dir]["status"] = "failed"
        _running_tasks[project_dir]["error"] = str(e)
        _running_tasks[project_dir]["end_time"] = datetime.now().isoformat()
        print(f"[main] 研究失败 [{project_dir}]: {e}")


# ==================== FastAPI 应用 ====================

@asynccontextmanager
async def lifespan(app: FastAPI):
    os.makedirs(PROJECTS_DIR, exist_ok=True)
    print("=" * 60)
    print(f"🔬 Deep Research 服务启动")
    print(f"📡 端口: {PORT}")
    print(f"📁 项目目录: {os.path.abspath(PROJECTS_DIR)}")
    print("=" * 60)
    yield
    print("\n✓ Deep Research 服务已关闭")


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

    # 读取 Researcher_list
    researcher_list_path = os.path.join(base, "Researcher_list.jsonl")
    researcher_list = _read_jsonl_safe(researcher_list_path)

    # 统计进度
    total = len(researcher_list)
    completed = sum(1 for r in researcher_list if r.get("status") == "completed")
    running = sum(1 for r in researcher_list if r.get("status") == "running")

    # 检查是否有最终报告
    report_files = glob.glob(os.path.join(base, "*.md"))
    has_report = len(report_files) > 0
    report_name = os.path.basename(report_files[0]) if report_files else None

    # 读取 doubt.jsonl
    doubt_path = os.path.join(base, "doubt.jsonl")
    doubts = _read_jsonl_safe(doubt_path)

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

    # 读取每个 Researcher 的 task_list
    researcher_tasks = {}
    for i in range(1, 6):
        r_id = f"Researcher{i}"
        task_path = os.path.join(base, r_id, "task_list.jsonl")
        tasks = _read_jsonl_safe(task_path)
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


@app.get("/projects")
async def list_projects():
    """列出所有历史研究项目"""
    if not os.path.exists(PROJECTS_DIR):
        return {"projects": []}

    projects = []
    for entry in sorted(os.scandir(PROJECTS_DIR), key=lambda e: e.name, reverse=True):
        if not entry.is_dir():
            continue

        base = entry.path
        researcher_list = _read_jsonl_safe(os.path.join(base, "Researcher_list.jsonl"))
        report_files = glob.glob(os.path.join(base, "*.md"))

        # 读取初始问题
        shared = _read_jsonl_safe(os.path.join(base, "shared_memory.jsonl"))
        question = ""
        for rec in shared:
            if rec.get("type") == "init":
                question = rec.get("user_question", "")
                break

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
    """安全读取 jsonl 文件"""
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
