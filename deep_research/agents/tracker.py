"""
Tracker — PostgreSQL 数据库检索智能体（留空，待完成）
"""

from .base_agent import BaseAgent, AgentResult


class Tracker(BaseAgent):
    agent_class = "Tracker"

    async def run(self) -> AgentResult:
        raise NotImplementedError("Tracker 尚未实现，请在此处接入 PostgreSQL 检索逻辑")
