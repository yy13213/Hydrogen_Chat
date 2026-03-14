"""
Probe — MySQL 数据库检索智能体（留空，待完成）
"""

from .base_agent import BaseAgent, AgentResult


class Probe(BaseAgent):
    agent_class = "Probe"

    async def run(self) -> AgentResult:
        raise NotImplementedError("Probe 尚未实现，请在此处接入 MySQL 检索逻辑")
