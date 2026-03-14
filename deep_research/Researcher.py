"""
Researcher.py — 研究员
负责：
1. 接受 Planner 分配的子研究，拆分为多个并行任务，调用 Agent
2. 每当某个 Agent 完成工作时激活，继续规划新任务
3. 最多串行 4 次，第 3 次起提示倒计时
4. 所有任务完成后通知 Planner
"""

import asyncio
import json
import os
from datetime import datetime

from google.genai import types
from pydantic import BaseModel, Field

from gemini_client import client, MODEL
from utils import generate_id, read_jsonl, write_jsonl_append, update_jsonl_record
from utils.file_lock import write_jsonl_all

PROJECTS_DIR = os.getenv("PROJECTS_DIR", "projects")
MAX_SERIAL_ROUNDS = 4


# ==================== 结构化返回模型 ====================

class AgentTask(BaseModel):
    task_id: str = Field(description="任务ID（留空，系统自动生成）")
    agent_class: str = Field(description="Agent类型：Archivist/Tracker/Probe/Builder/Rover/Insight")
    background: str = Field(description="任务背景")
    goal: str = Field(description="任务目标")
    parallel_count: int = Field(default=1, description="该Agent并行调用次数（通常为1）")


class ResearcherInitResponse(BaseModel):
    tasks: list[AgentTask] = Field(description="初始任务列表")
    analysis: str = Field(description="对子研究的分析")


class ResearcherContinueResponse(BaseModel):
    continue_research: bool = Field(description="是否需要继续研究")
    tasks: list[AgentTask] = Field(description="新的任务列表，若不需要继续则为空列表")
    reason: str = Field(description="决策理由")


# ==================== 辅助函数 ====================

def _get_paths(project_dir: str, researcher_id: str) -> dict:
    base = os.path.join(PROJECTS_DIR, project_dir)
    r_dir = os.path.join(base, researcher_id)
    return {
        "base": base,
        "shared_memory": os.path.join(base, "shared_memory.jsonl"),
        "researcher_list": os.path.join(base, "Researcher_list.jsonl"),
        "r_dir": r_dir,
        "task_list": os.path.join(r_dir, "task_list.jsonl"),
        "memory": os.path.join(r_dir, "memory.jsonl"),
    }


async def _call_gemini_with_retry(prompt: str, response_schema, max_retries: int = 3):
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


def _load_agent_config() -> dict:
    config_path = os.path.join(os.path.dirname(__file__), "agents", "agent_config.json")
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _build_context(paths: dict) -> str:
    shared = read_jsonl(paths["shared_memory"])
    memory = read_jsonl(paths["memory"])
    task_list = read_jsonl(paths["task_list"])
    return json.dumps({
        "shared_memory": shared,
        "my_memory": memory,
        "my_task_list": task_list,
    }, ensure_ascii=False, indent=2)


# ==================== Researcher 核心逻辑 ====================

class Researcher:
    def __init__(self, project_dir: str, researcher_id: str):
        self.project_dir = project_dir
        self.researcher_id = researcher_id
        self.paths = _get_paths(project_dir, researcher_id)
        self.serial_round = 0
        self.agent_config = _load_agent_config()
        os.makedirs(self.paths["r_dir"], exist_ok=True)

    async def start(self, sub_research_id: str, background: str, goal: str) -> None:
        self.serial_round = 1

        update_jsonl_record(
            self.paths["researcher_list"],
            lambda r: r.get("sub_research_id") == sub_research_id,
            lambda r: {**r, "status": "running", "start_time": datetime.now().isoformat()},
        )

        agent_config_str = json.dumps(self.agent_config, ensure_ascii=False, indent=2)
        context = _build_context(self.paths)

        prompt = f"""你是研究员 {self.researcher_id}，负责执行以下子研究：

子研究背景：{background}
子研究目标：{goal}

当前上下文：
{context}

可用的Agent及其介绍：
{agent_config_str}

请分析子研究内容，将其拆分为多个可并行的任务，分配给合适的Agent。
每个任务需要：
- agent_class：选择合适的Agent类型
- background：任务背景（包含子研究背景）
- goal：具体的任务目标
- parallel_count：该Agent并行调用次数（通常为1，复杂任务可设为2-3）

注意：
- 任务ID字段留空，系统自动生成
- 尽量让任务相互独立，可以并行进行
- 只选择已启用的Agent类型
"""
        result: ResearcherInitResponse = await _call_gemini_with_retry(prompt, ResearcherInitResponse)
        await self._dispatch_tasks(result.tasks, sub_research_id, background, goal)

    async def _dispatch_tasks(
        self,
        tasks: list[AgentTask],
        sub_research_id: str,
        background: str,
        goal: str,
    ) -> None:
        from agents.agent_runner import run_agent

        coroutines = []
        task_ids = []

        for task in tasks:
            count = max(1, task.parallel_count)
            for _ in range(count):
                task_id = generate_id()
                task_ids.append(task_id)
                now = datetime.now().isoformat()

                write_jsonl_append(self.paths["task_list"], {
                    "task_id": task_id,
                    "sub_research_id": sub_research_id,
                    "agent_class": task.agent_class,
                    "background": task.background,
                    "goal": task.goal,
                    "status": "running",
                    "start_time": now,
                    "end_time": None,
                })

                coroutines.append(
                    run_agent(
                        agent_class=task.agent_class,
                        task_id=task_id,
                        background=task.background,
                        goal=task.goal,
                        project_dir=self.project_dir,
                        researcher_id=self.researcher_id,
                    )
                )

        results = await asyncio.gather(*coroutines, return_exceptions=True)

        for task_id, result in zip(task_ids, results):
            if isinstance(result, Exception):
                update_jsonl_record(
                    self.paths["task_list"],
                    lambda r, tid=task_id: r.get("task_id") == tid,
                    lambda r: {**r, "status": "failed", "end_time": datetime.now().isoformat()},
                )
            else:
                update_jsonl_record(
                    self.paths["task_list"],
                    lambda r, tid=task_id: r.get("task_id") == tid,
                    lambda r: {**r, "status": "completed", "end_time": datetime.now().isoformat()},
                )

        await self.on_tasks_complete(sub_research_id, background, goal)

    async def on_tasks_complete(self, sub_research_id: str, background: str, goal: str) -> None:
        self.serial_round += 1

        if self.serial_round > MAX_SERIAL_ROUNDS:
            await self._notify_planner_complete(sub_research_id)
            return

        countdown_hint = ""
        if self.serial_round >= 3:
            remaining = MAX_SERIAL_ROUNDS - self.serial_round
            countdown_hint = f"\n\n⚠️ 倒计时提示：已进行 {self.serial_round} 轮研究，请在 {remaining} 轮内完成剩余研究！"

        agent_config_str = json.dumps(self.agent_config, ensure_ascii=False, indent=2)
        context = _build_context(self.paths)

        prompt = f"""你是研究员 {self.researcher_id}，正在执行子研究：

子研究背景：{background}
子研究目标：{goal}

当前上下文（包含已完成任务的结果）：
{context}

可用的Agent：
{agent_config_str}
{countdown_hint}

请分析当前研究进展，决定：
1. 是否需要继续研究（continue_research: true/false）
2. 如果需要，安排新的任务给Agent
3. 如果子研究目标已达成，返回 continue_research: false

注意：
- 只在子研究主题内进行研究，有限拓展，及时结束
- 所有必要结论得出后即可结束
"""
        result: ResearcherContinueResponse = await _call_gemini_with_retry(prompt, ResearcherContinueResponse)

        if not result.continue_research:
            await self._notify_planner_complete(sub_research_id)
            return

        await self._dispatch_tasks(result.tasks, sub_research_id, background, goal)

    async def _notify_planner_complete(self, sub_research_id: str) -> None:
        update_jsonl_record(
            self.paths["researcher_list"],
            lambda r: r.get("sub_research_id") == sub_research_id,
            lambda r: {**r, "status": "completed", "end_time": datetime.now().isoformat()},
        )

        from Planner import Planner
        planner = Planner(self.project_dir)
        completed = [
            r for r in read_jsonl(self.paths["researcher_list"])
            if r.get("status") == "completed"
        ]
        planner.serial_round = len(completed)
        await planner.on_researcher_complete(self.researcher_id)
