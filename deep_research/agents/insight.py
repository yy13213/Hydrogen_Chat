"""
Insight — 深度思考智能体
使用 Gemini thinking_level="high" 模式，处理复杂分析推理任务
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


class Insight(BaseAgent):
    agent_class = "Insight"

    async def run(self) -> AgentResult:
        context = self._build_context()

        prompt = f"""你是一位深度思考研究员（Insight），擅长深度分析和推理。

任务背景：{self.background}
任务目标：{self.goal}

当前研究上下文：
{context}

请对任务目标进行深度分析和推理。
要求：
1. 充分利用上下文中已有的研究信息
2. 进行深度逻辑推理，综合多方面信息
3. 识别潜在的关联、矛盾和规律
4. action 字段中详细描述分析思路、推理过程和关键洞察
5. conclusion 字段给出经过深度思考后的完整结论
6. sources 字段列出引用的上下文信息来源（如有）
"""
        for attempt in range(MAX_RETRIES):
            try:
                response = await asyncio.to_thread(
                    client.models.generate_content,
                    model=MODEL,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        thinking_config=types.ThinkingConfig(thinking_level="high"),
                        response_mime_type="application/json",
                        response_schema=AgentResult,
                    ),
                )
                data = json.loads(response.text)
                return AgentResult(**data)
            except Exception as e:
                self.log.warning(f"Insight 调用失败（第 {attempt+1} 次）: {e}")
                if attempt == MAX_RETRIES - 1:
                    self.log.error(f"Insight 最终失败: {e}", exc_info=True)
                    raise RuntimeError(f"Insight 调用失败: {e}") from e
                await asyncio.sleep(2 ** attempt)
