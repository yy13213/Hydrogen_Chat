"""
Rover — 网络搜索智能体
使用 Gemini 内置的 Google Search 工具在公共互联网搜索信息
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


class Rover(BaseAgent):
    agent_class = "Rover"

    async def run(self) -> AgentResult:
        context = self._build_context()

        prompt = f"""你是一位专业的网络搜索研究员（Rover）。

任务背景：{self.background}
任务目标：{self.goal}

当前研究上下文：
{context}

请使用 Google 搜索工具，在公共互联网上搜索与任务目标相关的信息。
要求：
1. 执行多次搜索，覆盖任务目标的各个方面
2. 记录每条信息的来源（URL、标题等）
3. 综合所有搜索结果，得出详细结论
4. action 字段中详细描述搜索过程、搜索词、找到的关键信息及其来源
5. conclusion 字段给出基于搜索结果的完整结论
6. sources 字段列出所有引用的信息来源URL
"""
        for attempt in range(MAX_RETRIES):
            try:
                response = await asyncio.to_thread(
                    client.models.generate_content,
                    model=MODEL,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        tools=[types.Tool(google_search=types.GoogleSearch())],
                        response_mime_type="application/json",
                        response_schema=AgentResult,
                    ),
                )
                data = json.loads(response.text)
                return AgentResult(**data)
            except Exception as e:
                if attempt == MAX_RETRIES - 1:
                    raise RuntimeError(f"Rover 调用失败: {e}") from e
                await asyncio.sleep(1)
