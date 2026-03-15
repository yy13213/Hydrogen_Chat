"""
Doubter.py — 质疑者
负责：
1. 收到 Planner 完成所有子研究的指令后，并行调用所有参与过研究的 Researcher 记忆
2. 分析 shared_memory 中的任务数据，提出必要质疑
3. 按被质疑 Researcher 分类，并行批量回答质疑（一次性结构化返回所有回答）
4. 并行调用质疑者 Researcher 判断是否接受回答
5. 对未被接受的质疑，调用 Researcher 进行补充研究
6. 完成后调用 Publisher
"""

import asyncio
import json
import os
from datetime import datetime
from typing import List

from google.genai import types
from pydantic import BaseModel, Field

from gemini_client import client, MODEL, PROJECTS_DIR
from logger import get_project_logger
from utils import generate_id, read_jsonl, write_jsonl_append, update_jsonl_record, update_all_jsonl_records

MAX_RETRIES = 3


# ==================== 结构化返回模型（全部使用具体 Pydantic 子模型，禁止 list[dict]）====================

class DoubtItem(BaseModel):
    task_id: str = Field(description="被质疑的任务ID")
    content: str = Field(description="质疑内容")


class DoubterAnalysisResponse(BaseModel):
    has_doubts: bool = Field(description="是否提出质疑")
    doubts: list[DoubtItem] = Field(default_factory=list, description="质疑列表")


class DoubtAnswer(BaseModel):
    doubt_id: str = Field(description="质疑记录的唯一ID（原样填写，不得修改）")
    answer: str = Field(description="对该质疑的详细回答内容")


class DoubtAnswerResponse(BaseModel):
    answers: list[DoubtAnswer] = Field(description="所有质疑的回答列表，一次性返回全部")


class DoubtReview(BaseModel):
    doubt_id: str = Field(description="质疑记录的唯一ID（原样填写，不得修改）")
    accepted: bool = Field(description="是否接受对方的回答")
    reason: str = Field(description="判断理由")


class DoubtAcceptResponse(BaseModel):
    reviews: list[DoubtReview] = Field(description="所有质疑的审核结果列表，一次性返回全部")


# ==================== 辅助函数 ====================

async def _call_gemini_with_retry(prompt: str, response_schema, log, max_retries: int = MAX_RETRIES):
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
            log.warning(f"Gemini 调用失败（第 {attempt+1} 次）: {e}")
            if attempt == max_retries - 1:
                log.error(f"Gemini 调用最终失败: {e}", exc_info=True)
                raise RuntimeError(f"Doubter 调用失败: {e}") from e
            await asyncio.sleep(2 ** attempt)


def _get_paths(project_dir: str) -> dict:
    base = os.path.join(PROJECTS_DIR, project_dir)
    return {
        "base": base,
        "shared_memory": os.path.join(base, "shared_memory.jsonl"),
        "researcher_list": os.path.join(base, "Researcher_list.jsonl"),
        "doubt": os.path.join(base, "doubt.jsonl"),
    }


def _get_participated_researchers(project_dir: str) -> List[str]:
    paths = _get_paths(project_dir)
    records = read_jsonl(paths["researcher_list"])
    return list({r["researcher_id"] for r in records if r.get("status") == "completed"})


def _build_researcher_context(project_dir: str, researcher_id: str) -> str:
    base = os.path.join(PROJECTS_DIR, project_dir)
    shared = read_jsonl(os.path.join(base, "shared_memory.jsonl"))
    memory = read_jsonl(os.path.join(base, researcher_id, "memory.jsonl"))
    task_list = read_jsonl(os.path.join(base, researcher_id, "task_list.jsonl"))
    return json.dumps({
        "shared_memory": shared,
        "memory": memory,
        "task_list": task_list,
    }, ensure_ascii=False, indent=2)


# ==================== Doubter 核心逻辑 ====================

class Doubter:
    def __init__(self, project_dir: str):
        self.project_dir = project_dir
        self.paths = _get_paths(project_dir)
        self.log = get_project_logger(project_dir, "Doubter")

    async def run(self) -> None:
        participated = _get_participated_researchers(self.project_dir)
        self.log.info(f"开始质疑流程，参与研究的 Researcher：{participated}")

        if not participated:
            self.log.warning("无参与研究的 Researcher，跳过质疑直接发布")
            await self._call_publisher()
            return

        self.log.info("阶段1：并行质疑分析")
        await self._phase_doubt(participated)

        doubt_records = read_jsonl(self.paths["doubt"])
        if not doubt_records:
            self.log.info("无任何质疑，直接进入发布阶段")
            await self._call_publisher()
            return

        self.log.info(f"共产生 {len(doubt_records)} 条质疑，进入回答阶段")
        self.log.info("阶段2：并行批量回答质疑")
        await self._phase_answer()

        self.log.info("阶段3：并行接受/拒绝判断")
        await self._phase_accept()

        self.log.info("阶段4：对未接受质疑进行补充研究")
        await self._phase_research_rejected()

        self.log.info("质疑流程完成，调用 Publisher")
        await self._call_publisher()

    async def _phase_doubt(self, researchers: List[str]) -> None:
        shared_memory = read_jsonl(self.paths["shared_memory"])
        shared_ctx = json.dumps(shared_memory, ensure_ascii=False, indent=2)

        async def analyze_as_researcher(researcher_id: str):
            context = _build_researcher_context(self.project_dir, researcher_id)
            prompt = f"""你是研究员 {researcher_id}，正在作为质疑者审查其他研究员的工作。

共享记忆（所有研究结论）：
{shared_ctx}

你自己的研究记忆：
{context}

请仔细分析 shared_memory 中所有子研究的任务数据，找出：
1. 结论与已知事实矛盾的任务
2. 逻辑推断不严谨的任务
3. 信源不可靠或缺乏依据的任务
4. 与你自己研究结论相矛盾的任务

注意：
- 仅提出必要的、有价值的质疑，不要无故质疑
- 可以不提出任何质疑（has_doubts: false）
- 每条质疑需指定具体的 task_id
"""
            result: DoubterAnalysisResponse = await _call_gemini_with_retry(
                prompt, DoubterAnalysisResponse, self.log
            )
            if result.has_doubts:
                task_to_researcher = self._build_task_to_researcher_map(shared_memory)
                for doubt in result.doubts:
                    task_id = doubt.task_id
                    doubted_researcher = task_to_researcher.get(task_id, "Unknown")
                    write_jsonl_append(self.paths["doubt"], {
                        "doubt_id": generate_id(),
                        "doubter": researcher_id,
                        "doubted": doubted_researcher,
                        "task_id": task_id,
                        "doubt_content": doubt.content,
                        "answer_content": "",
                        "accepted": None,
                        "reason": "",
                        "timestamp": datetime.now().isoformat(),
                    })
                self.log.info(f"{researcher_id} 提出 {len(result.doubts)} 条质疑")
            else:
                self.log.info(f"{researcher_id} 无质疑")

        await asyncio.gather(*[analyze_as_researcher(r) for r in researchers])

    def _build_task_to_researcher_map(self, shared_memory: list) -> dict:
        mapping = {}
        for rec in shared_memory:
            if rec.get("type") == "sub_research":
                r_id = rec.get("researcher_id", "")
                for task in rec.get("tasks", []):
                    mapping[task.get("task_id", "")] = r_id
        return mapping

    async def _phase_answer(self) -> None:
        """按被质疑 Researcher 分组，并行批量回答——每个 Researcher 一次性返回所有回答"""
        doubt_records = read_jsonl(self.paths["doubt"])
        unanswered = [d for d in doubt_records if not d.get("answer_content")]
        if not unanswered:
            self.log.info("无待回答的质疑")
            return

        # 按被质疑者分组
        grouped: dict[str, list] = {}
        for d in unanswered:
            grouped.setdefault(d.get("doubted", "Unknown"), []).append(d)

        async def answer_as_researcher(researcher_id: str, doubts: list):
            context = _build_researcher_context(self.project_dir, researcher_id)

            # 整理所有质疑内容，要求一次性批量回答，用 doubt_id 作为唯一标识
            doubts_summary = "\n".join([
                f"- doubt_id: {d['doubt_id']} | 任务ID: {d['task_id']} | 质疑者: {d['doubter']}\n  质疑内容: {d['doubt_content']}"
                for d in doubts
            ])

            prompt = f"""你是研究员 {researcher_id}，需要一次性回答所有针对你工作的质疑。

你的研究上下文：
{context}

以下是所有针对你的质疑（共 {len(doubts)} 条），请逐条认真回答：
{doubts_summary}

要求：
- 在 answers 列表中，为每条质疑提供一个回答条目
- doubt_id 必须原样填写（不得修改），用于精确匹配
- answer 提供详细、有据可查的回答，承认错误或提供补充说明
- 必须覆盖全部 {len(doubts)} 条质疑，不得遗漏
"""
            result: DoubtAnswerResponse = await _call_gemini_with_retry(
                prompt, DoubtAnswerResponse, self.log
            )

            # 按 doubt_id 精确更新
            answered_ids = {ans.doubt_id for ans in result.answers}
            for ans in result.answers:
                update_jsonl_record(
                    self.paths["doubt"],
                    lambda r, did=ans.doubt_id: r.get("doubt_id") == did,
                    lambda r, a=ans.answer: {**r, "answer_content": a},
                )
            self.log.info(f"{researcher_id} 一次性回答了 {len(result.answers)} 条质疑（doubt_ids: {answered_ids}）")

        await asyncio.gather(*[
            answer_as_researcher(r_id, doubts)
            for r_id, doubts in grouped.items()
        ])

    async def _phase_accept(self) -> None:
        """按质疑者分组，并行批量审核——每个质疑者一次性返回所有审核结果"""
        doubt_records = read_jsonl(self.paths["doubt"])
        answered = [d for d in doubt_records if d.get("answer_content") and d.get("accepted") is None]
        if not answered:
            self.log.info("无待审核的质疑回答")
            return

        # 按质疑者分组
        grouped: dict[str, list] = {}
        for d in answered:
            grouped.setdefault(d.get("doubter", "Unknown"), []).append(d)

        async def review_as_doubter(researcher_id: str, doubts: list):
            context = _build_researcher_context(self.project_dir, researcher_id)

            doubts_summary = "\n".join([
                f"- doubt_id: {d['doubt_id']} | 被质疑者: {d['doubted']}\n  我的质疑: {d['doubt_content']}\n  对方回答: {d['answer_content']}"
                for d in doubts
            ])

            prompt = f"""你是研究员 {researcher_id}，你之前提出了若干质疑，现在需要一次性判断是否接受对方的所有回答。

你的研究上下文：
{context}

以下是你的所有质疑及对方的回答（共 {len(doubts)} 条）：
{doubts_summary}

要求：
- 在 reviews 列表中，为每条质疑提供审核结果
- doubt_id 必须原样填写（不得修改），用于精确匹配
- accepted 填写是否接受（true/false）
- reason 简述判断理由
- 必须覆盖全部 {len(doubts)} 条，不得遗漏
"""
            result: DoubtAcceptResponse = await _call_gemini_with_retry(
                prompt, DoubtAcceptResponse, self.log
            )

            for review in result.reviews:
                update_jsonl_record(
                    self.paths["doubt"],
                    lambda r, did=review.doubt_id: r.get("doubt_id") == did,
                    lambda r, rv=review: {
                        **r,
                        "accepted": rv.accepted,
                        "reason": rv.reason,
                    },
                )
            accepted_count = sum(1 for rv in result.reviews if rv.accepted)
            self.log.info(f"{researcher_id} 审核完成：{accepted_count}/{len(result.reviews)} 条接受")

        await asyncio.gather(*[
            review_as_doubter(r_id, doubts)
            for r_id, doubts in grouped.items()
        ])

    async def _phase_research_rejected(self) -> None:
        doubt_records = read_jsonl(self.paths["doubt"])
        rejected = [d for d in doubt_records if d.get("accepted") is False]
        if not rejected:
            self.log.info("无被拒绝的质疑，无需补充研究")
            return

        self.log.info(f"有 {len(rejected)} 条质疑未被接受，启动补充研究")
        from Researcher import Researcher

        tasks = []
        for doubt in rejected:
            researcher_list = read_jsonl(self.paths["researcher_list"])
            busy = {r["researcher_id"] for r in researcher_list if r.get("status") == "running"}
            all_r = {f"Researcher{i}" for i in range(1, 6)}
            idle = list(all_r - busy)
            target_r = idle[0] if idle else doubt.get("doubted", "Researcher1")

            sub_id = generate_id()
            background = (
                f"质疑补充研究\n"
                f"原质疑：{doubt.get('doubt_content', '')}\n"
                f"被质疑方回答：{doubt.get('answer_content', '')}"
            )
            goal = f"验证并补充说明：{doubt.get('doubt_content', '')}"

            write_jsonl_append(self.paths["researcher_list"], {
                "sub_research_id": sub_id,
                "researcher_id": target_r,
                "background": background,
                "goal": goal,
                "status": "pending",
                "start_time": datetime.now().isoformat(),
                "end_time": None,
                "is_doubt_research": True,
            })

            self.log.info(f"补充研究 [{sub_id}] → {target_r}：{goal[:60]}...")
            researcher = Researcher(self.project_dir, target_r)
            tasks.append(researcher.start(sub_id, background, goal))

        results = await asyncio.gather(*tasks, return_exceptions=True)
        for i, res in enumerate(results):
            if isinstance(res, Exception):
                self.log.error(f"补充研究 #{i} 失败: {res}", exc_info=False)

    async def _call_publisher(self) -> None:
        from Publisher import Publisher
        publisher = Publisher(self.project_dir)
        await publisher.run()
