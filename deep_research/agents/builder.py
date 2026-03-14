"""
Builder — 编程智能体
使用 Gemini 代码执行工具，编写并运行代码完成计算/统计任务
"""

import asyncio
import json
import os
import sys

from google.genai import types

_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _root not in sys.path:
    sys.path.insert(0, _root)

from gemini_client import client, MODEL
from .base_agent import BaseAgent, AgentResult

MAX_RETRIES = 3


class Builder(BaseAgent):
    agent_class = "Builder"

    async def run(self) -> AgentResult:
        context = self._build_context()

        prompt = f"""你是一位专业的编程研究员（Builder）。

任务背景：{self.background}
任务目标：{self.goal}

当前研究上下文：
{context}

请编写并执行代码来完成任务目标。
要求：
1. 分析任务，确定需要编写什么代码
2. 编写并执行代码，完成计算、统计或数据处理
3. 记录代码执行过程和输出结果
4. action 字段中详细描述编写的代码逻辑、执行步骤和输出
5. conclusion 字段给出基于代码执行结果的完整结论
6. sources 字段列出使用的数据来源（如有）
"""
        for attempt in range(MAX_RETRIES):
            try:
                response = await asyncio.to_thread(
                    client.models.generate_content,
                    model=MODEL,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        tools=[types.Tool(code_execution=types.ToolCodeExecution())],
                        response_mime_type="application/json",
                        response_schema=AgentResult,
                    ),
                )
                data = json.loads(response.text)
                return AgentResult(**data)
            except Exception as e:
                if attempt == MAX_RETRIES - 1:
                    raise RuntimeError(f"Builder 调用失败: {e}") from e
                await asyncio.sleep(1)
