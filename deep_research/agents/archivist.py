"""
Archivist — 知识库检索智能体（留空，待完成）
"""

from .base_agent import BaseAgent, AgentResult


class Archivist(BaseAgent):
    agent_class = "Archivist"

    async def run(self) -> AgentResult:
        raise NotImplementedError("Archivist 尚未实现，请在此处接入知识库检索逻辑")
