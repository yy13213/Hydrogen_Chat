"""
base_agent.py — Agent 基类
所有 Agent 继承此类，实现 run() 方法
"""

import asyncio
import json
import os
import sys
from abc import ABC, abstractmethod
from datetime import datetime

from google.genai import types
from pydantic import BaseModel, Field

# 确保 deep_research 根目录在 sys.path 中（agents 子包调用时需要）
_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _root not in sys.path:
    sys.path.insert(0, _root)

from gemini_client import client, MODEL
from utils import read_jsonl, write_jsonl_append
from utils.file_lock import write_jsonl_all

PROJECTS_DIR = os.getenv("PROJECTS_DIR", "projects")


class AgentResult(BaseModel):
    action: str = Field(description="执行过程：详细描述执行步骤、搜集的信息及其信源")
    conclusion: str = Field(description="详细结论：基于行动得出的完整结论")
    sources: list[str] = Field(default_factory=list, description="信息来源列表")


class BaseAgent(ABC):
    agent_class: str = "BaseAgent"

    def __init__(
        self,
        task_id: str,
        background: str,
        goal: str,
        project_dir: str,
        researcher_id: str,
    ):
        self.task_id = task_id
        self.background = background
        self.goal = goal
        self.project_dir = project_dir
        self.researcher_id = researcher_id

        base = os.path.join(PROJECTS_DIR, project_dir)
        r_dir = os.path.join(base, researcher_id)
        self.paths = {
            "base": base,
            "shared_memory": os.path.join(base, "shared_memory.jsonl"),
            "memory": os.path.join(r_dir, "memory.jsonl"),
            "task_list": os.path.join(r_dir, "task_list.jsonl"),
        }

    def _build_context(self) -> str:
        shared = read_jsonl(self.paths["shared_memory"])
        memory = read_jsonl(self.paths["memory"])
        return json.dumps(
            {"shared_memory": shared, "researcher_memory": memory},
            ensure_ascii=False,
            indent=2,
        )

    @abstractmethod
    async def run(self) -> AgentResult:
        """执行任务，返回结构化结果"""
        ...

    async def execute(self) -> AgentResult:
        """
        执行任务的完整流程：
        1. 调用 run() 获取结果
        2. 组装 STAR 记忆体
        3. 调用 Supervisor 评分
        4. 写入 memory.jsonl 和 shared_memory.jsonl
        """
        result = await self.run()

        star = {
            "task_id": self.task_id,
            "agent_class": self.agent_class,
            "S": self.background,
            "T": self.goal,
            "A": result.action,
            "R": result.conclusion,
            "sources": result.sources,
            "C": 0,
        }

        from Supervisor import Supervisor
        supervisor = Supervisor(self.project_dir, self.researcher_id)
        credibility, refined_action, refined_conclusion = await supervisor.evaluate(star)

        star["C"] = credibility
        write_jsonl_append(self.paths["memory"], star)
        self._update_shared_memory(refined_action, refined_conclusion, credibility)

        return result

    def _update_shared_memory(
        self, refined_action: str, refined_conclusion: str, credibility: int
    ) -> None:
        records = read_jsonl(self.paths["shared_memory"])
        updated = False

        for rec in records:
            if (
                rec.get("type") == "sub_research"
                and rec.get("researcher_id") == self.researcher_id
            ):
                tasks = rec.get("tasks", [])
                tasks.append({
                    "task_id": self.task_id,
                    "action": refined_action,
                    "conclusion": refined_conclusion,
                    "credibility": credibility,
                })
                rec["tasks"] = tasks
                updated = True
                break

        if updated:
            write_jsonl_all(self.paths["shared_memory"], records)
        else:
            write_jsonl_append(self.paths["shared_memory"], {
                "type": "task_result",
                "task_id": self.task_id,
                "researcher_id": self.researcher_id,
                "agent_class": self.agent_class,
                "action": refined_action,
                "conclusion": refined_conclusion,
                "credibility": credibility,
            })
