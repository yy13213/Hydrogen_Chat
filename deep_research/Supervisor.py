"""
Supervisor.py — 主管
负责：
1. 接受 Agent 完成任务得到的 STAR 记忆体
2. 参考信源与结论，给出可信度评分（百分制）
3. 精炼研究内容和结论到500字以内
4. 结构化返回评分结果
"""

import asyncio
import json
import os
from typing import Tuple

from google.genai import types
from pydantic import BaseModel, Field

from gemini_client import client, MODEL, PROJECTS_DIR
from logger import get_project_logger
from utils import read_jsonl

MAX_RETRIES = 3


class SupervisorEvaluation(BaseModel):
    credibility: int = Field(description="可信度评分（0-100分）")
    refined_action: str = Field(description="精炼后的研究行动（500字以内）")
    refined_conclusion: str = Field(description="精炼后的研究结论（500字以内）")
    evaluation_reason: str = Field(description="评分理由")


class Supervisor:
    def __init__(self, project_dir: str, researcher_id: str):
        self.project_dir = project_dir
        self.researcher_id = researcher_id
        self.log = get_project_logger(project_dir, f"Supervisor.{researcher_id}")

        base = os.path.join(PROJECTS_DIR, project_dir)
        r_dir = os.path.join(base, researcher_id)
        self.paths = {
            "shared_memory": os.path.join(base, "shared_memory.jsonl"),
            "memory": os.path.join(r_dir, "memory.jsonl"),
        }

    async def evaluate(self, star: dict) -> Tuple[int, str, str]:
        """评估 STAR 记忆体，返回 (可信度, 精炼行动, 精炼结论)"""
        shared_ctx = json.dumps(read_jsonl(self.paths["shared_memory"]), ensure_ascii=False, indent=2)
        memory_ctx = json.dumps(read_jsonl(self.paths["memory"]), ensure_ascii=False, indent=2)

        prompt = f"""你是一位严格的研究主管（Supervisor），负责评估研究员的工作质量。

当前共享记忆（研究背景）：
{shared_ctx}

{self.researcher_id} 的历史记忆：
{memory_ctx}

需要评估的 STAR 记忆体：
- 情景（S）：{star.get('S', '')}
- 目标（T）：{star.get('T', '')}
- 行动（A）：{star.get('A', '')}
- 结论（R）：{star.get('R', '')}
- 信息来源：{json.dumps(star.get('sources', []), ensure_ascii=False)}

请完成以下评估：
1. 可信度评分（0-100分）：
   - 90-100：信源权威，结论严谨，逻辑清晰，无过度推导
   - 70-89：信源较可靠，结论基本合理
   - 50-69：信源一般，结论有一定依据但不够充分
   - 30-49：信源不明或结论推断成分较多
   - 0-29：无可靠信源，结论主观臆断
   如果发现一条伪造的引用，该任务的可信度评分（C）直接扣除 20 分。

2. 精炼行动（500字以内）：保留关键执行步骤和重要信息来源

3. 精炼结论（500字以内）：提炼核心结论，去除冗余，记录你在记忆生成过程中发现的、与普遍认知不符或具有冲突的数据点。

4. 评分理由：简述评分依据
"""
        for attempt in range(MAX_RETRIES):
            try:
                response = await asyncio.to_thread(
                    client.models.generate_content,
                    model=MODEL,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=SupervisorEvaluation,
                    ),
                )
                data = json.loads(response.text)
                result = SupervisorEvaluation(**data)
                credibility = max(0, min(100, result.credibility))
                self.log.info(f"任务 [{star.get('task_id', '?')}] 评分：{credibility}分")
                return credibility, result.refined_action, result.refined_conclusion
            except Exception as e:
                self.log.warning(f"Supervisor 评估失败（第 {attempt+1} 次）: {e}")
                if attempt == MAX_RETRIES - 1:
                    self.log.error(f"Supervisor 评估最终失败: {e}", exc_info=True)
                    raise RuntimeError(f"Supervisor 评估失败（已重试 {MAX_RETRIES} 次）: {e}") from e
                await asyncio.sleep(2 ** attempt)
