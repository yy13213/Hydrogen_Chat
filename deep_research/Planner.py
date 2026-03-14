"""
Planner.py — 规划者
负责：
1. 研究开始时建立项目目录，重写问题，拆分子研究，调用 Researcher
2. 每当某个 Researcher 完成工作时激活，继续规划新的子研究
3. 当某个 Researcher 超时（>300s）时激活，拆分其工作给空闲 Researcher
4. 最多串行 6 次研究，第 4 次起提示倒计时
5. 所有子研究完成后调用 Doubter
"""

import asyncio
import os
import shutil
from datetime import datetime
from typing import Optional

from google.genai import types
from pydantic import BaseModel, Field

from gemini_client import client, MODEL
from utils import generate_id, read_jsonl, write_jsonl_append, update_jsonl_record
from utils.file_lock import write_jsonl_all

PROJECTS_DIR = os.getenv("PROJECTS_DIR", "projects")
MAX_RESEARCHERS = 5
MAX_SERIAL_ROUNDS = 8
RESEARCHER_TIMEOUT = 300


# ==================== 结构化返回模型 ====================

class SubResearch(BaseModel):
    sub_research_id: str = Field(description="子研究ID（由框架生成，留空即可）")
    researcher_id: str = Field(description="分配的Researcher编号，如 Researcher1")
    background: str = Field(description="子研究背景")
    goal: str = Field(description="子研究目标")


class PlannerInitResponse(BaseModel):
    rewritten_question: str = Field(description="重写后的研究问题")
    sub_researches: list[SubResearch] = Field(description="初始子研究列表")


class PlannerContinueResponse(BaseModel):
    continue_research: bool = Field(description="是否需要继续研究")
    sub_researches: list[SubResearch] = Field(description="新的子研究列表，若不需要继续则为空列表")
    reason: str = Field(description="决策理由")


class PlannerTimeoutResponse(BaseModel):
    split_tasks: list[dict] = Field(description="拆分给空闲Researcher的任务列表，每项包含researcher_id, background, goal")
    reason: str = Field(description="拆分理由")


# ==================== 辅助函数 ====================

def _get_project_paths(project_dir: str) -> dict:
    base = os.path.join(PROJECTS_DIR, project_dir)
    return {
        "base": base,
        "shared_memory": os.path.join(base, "shared_memory.jsonl"),
        "researcher_list": os.path.join(base, "Researcher_list.jsonl"),
        "article": os.path.join(base, "article.json"),
        "doubt": os.path.join(base, "doubt.jsonl"),
        "report": os.path.join(base, "research_report.md"),
    }


def _researcher_dir(project_dir: str, researcher_id: str) -> str:
    return os.path.join(PROJECTS_DIR, project_dir, researcher_id)


def _researcher_paths(project_dir: str, researcher_id: str) -> dict:
    d = _researcher_dir(project_dir, researcher_id)
    return {
        "dir": d,
        "task_list": os.path.join(d, "task_list.jsonl"),
        "memory": os.path.join(d, "memory.jsonl"),
    }


async def _call_gemini_with_retry(prompt: str, response_schema, max_retries: int = 3):
    """使用最新 google-genai SDK 调用 Gemini，结构化返回，最多重试 3 次"""
    import json
    for attempt in range(max_retries):
        try:
            response = await asyncio.to_thread(
                client.models.generate_content,
                model=MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=response_schema,
                ),
            )
            data = json.loads(response.text)
            return response_schema(**data)
        except Exception as e:
            if attempt == max_retries - 1:
                raise RuntimeError(f"Gemini 调用失败（已重试 {max_retries} 次）: {e}") from e
            await asyncio.sleep(1)


def _build_shared_memory_context(paths: dict) -> str:
    import json
    records = read_jsonl(paths["shared_memory"])
    if not records:
        return "（暂无共享记忆）"
    return json.dumps(records, ensure_ascii=False, indent=2)


def _build_researcher_list_context(paths: dict) -> str:
    import json
    records = read_jsonl(paths["researcher_list"])
    if not records:
        return "（暂无Researcher列表）"
    return json.dumps(records, ensure_ascii=False, indent=2)


# ==================== Planner 核心逻辑 ====================

class Planner:
    def __init__(self, project_dir: str):
        self.project_dir = project_dir
        self.paths = _get_project_paths(project_dir)
        self.serial_round = 0

    @classmethod
    def create_project(cls) -> "Planner":
        """创建新项目目录，返回 Planner 实例"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        project_dir = timestamp
        base = os.path.join(PROJECTS_DIR, project_dir)
        os.makedirs(base, exist_ok=True)
        for i in range(1, MAX_RESEARCHERS + 1):
            os.makedirs(os.path.join(base, f"Researcher{i}"), exist_ok=True)
        return cls(project_dir)

    async def init_research(self, user_question: str) -> str:
        """研究初始化：重写问题，拆分子研究，写入文件，返回项目目录名"""
        self.serial_round = 1
        paths = self.paths

        write_jsonl_append(paths["shared_memory"], {
            "type": "init",
            "user_question": user_question,
            "rewritten_question": "",
        })

        prompt = f"""你是一位深度研究规划者。
用户问题：{user_question}

请完成以下任务：
1. 对用户问题进行重写，使其更清晰、更适合深度研究
2. 将研究拆分为多个可并行的子研究（最多5个），为每个子研究分配一个Researcher（Researcher1-Researcher5）
3. 每个子研究需要有明确的背景和目标

注意：
- 子研究ID字段留空，系统会自动生成
- 尽量让子研究相互独立，可以并行进行
- 每个子研究要有清晰的研究边界
"""
        result: PlannerInitResponse = await _call_gemini_with_retry(prompt, PlannerInitResponse)

        records = read_jsonl(paths["shared_memory"])
        for rec in records:
            if rec.get("type") == "init":
                rec["rewritten_question"] = result.rewritten_question
        write_jsonl_all(paths["shared_memory"], records)

        for sub in result.sub_researches:
            sub_id = generate_id()
            now = datetime.now().isoformat()
            researcher_id = sub.researcher_id

            write_jsonl_append(paths["researcher_list"], {
                "sub_research_id": sub_id,
                "researcher_id": researcher_id,
                "background": sub.background,
                "goal": sub.goal,
                "status": "pending",
                "start_time": now,
                "end_time": None,
            })

            write_jsonl_append(paths["shared_memory"], {
                "type": "sub_research",
                "sub_research_id": sub_id,
                "researcher_id": researcher_id,
                "background": sub.background,
                "goal": sub.goal,
                "tasks": [],
            })

            r_paths = _researcher_paths(self.project_dir, researcher_id)
            os.makedirs(r_paths["dir"], exist_ok=True)

        return self.project_dir

    async def on_researcher_complete(self, researcher_id: str) -> bool:
        """某个 Researcher 完成后激活 Planner。返回 True 表示继续研究。"""
        self.serial_round += 1
        paths = self.paths

        update_jsonl_record(
            paths["researcher_list"],
            lambda r: r.get("researcher_id") == researcher_id and r.get("status") == "running",
            lambda r: {**r, "status": "completed", "end_time": datetime.now().isoformat()},
        )

        if self.serial_round >= MAX_SERIAL_ROUNDS:
            return await self._finalize_research()

        countdown_hint = ""
        if self.serial_round >= 4:
            remaining = MAX_SERIAL_ROUNDS - self.serial_round
            countdown_hint = f"\n\n⚠️ 倒计时提示：已进行 {self.serial_round} 轮研究，请在 {remaining} 轮内完成剩余研究！"

        shared_ctx = _build_shared_memory_context(paths)
        researcher_list_ctx = _build_researcher_list_context(paths)

        all_records = read_jsonl(paths["researcher_list"])
        busy_researchers = {r["researcher_id"] for r in all_records if r.get("status") == "running"}
        all_researchers = {f"Researcher{i}" for i in range(1, MAX_RESEARCHERS + 1)}
        idle_researchers = all_researchers - busy_researchers

        prompt = f"""你是一位深度研究规划者。
当前 {researcher_id} 已完成工作。

共享记忆（已完成的研究内容）：
{shared_ctx}

Researcher任务列表：
{researcher_list_ctx}

当前空闲的Researcher：{', '.join(idle_researchers) if idle_researchers else '无'}
{countdown_hint}

请分析当前研究进展，决定：
1. 是否需要继续研究（continue_research: true/false）
2. 如果需要，为空闲的Researcher安排新的子研究任务
3. 注意：每个Researcher有自己的记忆，建议为同一Researcher分配相关工作；新工作尽量给未参与过的Researcher
4. 如果所有研究目标已达成，返回 continue_research: false

注意：Planner无需规划撰写报告，只需把研究所需要的所有结论得出即可。
"""
        result: PlannerContinueResponse = await _call_gemini_with_retry(prompt, PlannerContinueResponse)

        if not result.continue_research:
            return await self._finalize_research()

        for sub in result.sub_researches:
            sub_id = generate_id()
            now = datetime.now().isoformat()
            r_id = sub.researcher_id

            write_jsonl_append(paths["researcher_list"], {
                "sub_research_id": sub_id,
                "researcher_id": r_id,
                "background": sub.background,
                "goal": sub.goal,
                "status": "pending",
                "start_time": now,
                "end_time": None,
            })

            write_jsonl_append(paths["shared_memory"], {
                "type": "sub_research",
                "sub_research_id": sub_id,
                "researcher_id": r_id,
                "background": sub.background,
                "goal": sub.goal,
                "tasks": [],
            })

            r_paths = _researcher_paths(self.project_dir, r_id)
            os.makedirs(r_paths["dir"], exist_ok=True)

        return True

    async def on_researcher_timeout(self, researcher_id: str) -> None:
        """某个 Researcher 超时（>300s）时激活，拆分其工作给空闲 Researcher"""
        import json
        paths = self.paths
        r_paths = _researcher_paths(self.project_dir, researcher_id)

        memory_records = read_jsonl(r_paths["memory"])
        task_records = read_jsonl(r_paths["task_list"])
        pending_tasks = [t for t in task_records if t.get("status") == "pending"]

        all_records = read_jsonl(paths["researcher_list"])
        busy_researchers = {r["researcher_id"] for r in all_records if r.get("status") == "running"}
        all_researchers = {f"Researcher{i}" for i in range(1, MAX_RESEARCHERS + 1)}
        idle_researchers = list(all_researchers - busy_researchers - {researcher_id})

        if not idle_researchers or not pending_tasks:
            return

        prompt = f"""你是一位深度研究规划者。
{researcher_id} 已运行超过300秒，需要拆分其剩余工作。

{researcher_id} 的记忆：
{json.dumps(memory_records, ensure_ascii=False, indent=2)}

{researcher_id} 未完成的任务：
{json.dumps(pending_tasks, ensure_ascii=False, indent=2)}

当前空闲的Researcher：{', '.join(idle_researchers)}

请将未完成的任务合理拆分给空闲的Researcher，结构化返回拆分方案。
每项包含：researcher_id（目标Researcher）、background（任务背景）、goal（任务目标）。
"""
        result: PlannerTimeoutResponse = await _call_gemini_with_retry(prompt, PlannerTimeoutResponse)

        for task in result.split_tasks:
            target_r = task.get("researcher_id")
            if not target_r:
                continue

            sub_id = generate_id()
            now = datetime.now().isoformat()

            write_jsonl_append(paths["researcher_list"], {
                "sub_research_id": sub_id,
                "researcher_id": target_r,
                "background": task.get("background", ""),
                "goal": task.get("goal", ""),
                "status": "pending",
                "start_time": now,
                "end_time": None,
            })

            write_jsonl_append(paths["shared_memory"], {
                "type": "sub_research",
                "sub_research_id": sub_id,
                "researcher_id": target_r,
                "background": task.get("background", ""),
                "goal": task.get("goal", ""),
                "tasks": [],
            })

            target_r_paths = _researcher_paths(self.project_dir, target_r)
            os.makedirs(target_r_paths["dir"], exist_ok=True)
            if os.path.exists(r_paths["memory"]):
                shutil.copy2(r_paths["memory"], target_r_paths["memory"])

    async def _finalize_research(self) -> bool:
        """所有研究完成，调用 Doubter"""
        from Doubter import Doubter
        doubter = Doubter(self.project_dir)
        await doubter.run()
        return False
