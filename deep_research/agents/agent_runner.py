"""
agent_runner.py — Agent 统一调度入口
根据 agent_class 实例化对应 Agent 并执行
"""

import json
import os
from typing import TYPE_CHECKING

from .base_agent import AgentResult

AGENT_REGISTRY = {
    "Archivist": "archivist.Archivist",
    "Tracker": "tracker.Tracker",
    "Probe": "probe.Probe",
    "Builder": "builder.Builder",
    "Rover": "rover.Rover",
    "Insight": "insight.Insight",
}


def _load_agent_config() -> dict:
    config_path = os.path.join(os.path.dirname(__file__), "agent_config.json")
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"agents": {}}


def _get_agent_class(agent_class: str):
    """动态导入 Agent 类"""
    if agent_class not in AGENT_REGISTRY:
        raise ValueError(f"未知的 Agent 类型: {agent_class}")

    module_path, class_name = AGENT_REGISTRY[agent_class].rsplit(".", 1)
    import importlib
    module = importlib.import_module(f".{module_path}", package="agents")
    return getattr(module, class_name)


async def run_agent(
    agent_class: str,
    task_id: str,
    background: str,
    goal: str,
    project_dir: str,
    researcher_id: str,
) -> AgentResult:
    """
    实例化并执行指定类型的 Agent
    """
    config = _load_agent_config()
    agent_cfg = config.get("agents", {}).get(agent_class, {})

    if not agent_cfg.get("enabled", False):
        raise ValueError(f"Agent {agent_class} 未启用，请在 agent_config.json 中启用")

    AgentClass = _get_agent_class(agent_class)
    agent = AgentClass(
        task_id=task_id,
        background=background,
        goal=goal,
        project_dir=project_dir,
        researcher_id=researcher_id,
    )
    return await agent.execute()
