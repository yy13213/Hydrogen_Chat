"""
Publisher.py — 稿件发布者（主编）
负责：
1. 以 shared_memory 和 Researcher_list 为上下文，规划文章章节，分配给 Researcher 撰写
2. 并行调用所有 Researcher 的记忆（含质疑表中针对自己的质疑、回答及评价），逐章节撰写，存入 article.json
3. 以 article.json 为上下文，调用 Gemini 整理为 Markdown 格式（利用其超长上下文和输出上限）
4. 对 research_report.md 重命名，输出最终结果
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
from utils import read_jsonl
from utils.file_lock import write_json

MAX_RETRIES = 3


# ==================== 结构化返回模型 ====================

class ChapterPlan(BaseModel):
    chapter_index: int = Field(description="章节序号（从1开始）")
    title: str = Field(description="章节标题")
    description: str = Field(description="章节内容说明")
    researcher_id: str = Field(description="负责撰写的Researcher编号")


class PublisherPlanResponse(BaseModel):
    article_title: str = Field(description="文章标题")
    chapters: list[ChapterPlan] = Field(description="章节规划列表（即为文章最终呈现顺序）")


class ChapterContent(BaseModel):
    chapter_index: int = Field(description="章节序号")
    title: str = Field(description="章节标题")
    content: str = Field(description="章节正文内容")


class ReportNameResponse(BaseModel):
    report_name: str = Field(description="报告文件名（不含扩展名，使用中文或英文，避免特殊字符）")


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
                raise RuntimeError(f"Publisher Gemini 调用失败: {e}") from e
            await asyncio.sleep(2 ** attempt)


def _get_paths(project_dir: str) -> dict:
    base = os.path.join(PROJECTS_DIR, project_dir)
    return {
        "base": base,
        "shared_memory": os.path.join(base, "shared_memory.jsonl"),
        "researcher_list": os.path.join(base, "Researcher_list.jsonl"),
        "article": os.path.join(base, "article.json"),
        "report": os.path.join(base, "research_report.md"),
    }


def _researcher_context(project_dir: str, researcher_id: str) -> str:
    """构建 Researcher 撰写章节时的完整上下文，包含质疑表中针对自己的质疑、回答及评价"""
    base = os.path.join(PROJECTS_DIR, project_dir)
    shared = read_jsonl(os.path.join(base, "shared_memory.jsonl"))
    memory = read_jsonl(os.path.join(base, researcher_id, "memory.jsonl"))
    task_list = read_jsonl(os.path.join(base, researcher_id, "task_list.jsonl"))

    # 从质疑表中提取与该 Researcher 相关的记录（被质疑 或 作为质疑者）
    doubt_path = os.path.join(base, "doubt.jsonl")
    all_doubts = read_jsonl(doubt_path)
    relevant_doubts = [
        {
            "doubt_id": d.get("doubt_id"),
            "role": "被质疑方" if d.get("doubted") == researcher_id else "质疑方",
            "doubter": d.get("doubter"),
            "doubted": d.get("doubted"),
            "task_id": d.get("task_id"),
            "doubt_content": d.get("doubt_content"),
            "answer_content": d.get("answer_content", ""),
            "accepted": d.get("accepted"),
            "reason": d.get("reason", ""),
        }
        for d in all_doubts
        if d.get("doubted") == researcher_id or d.get("doubter") == researcher_id
    ]

    return json.dumps({
        "shared_memory": shared,
        "memory": memory,
        "task_list": task_list,
        "doubt_records": relevant_doubts,
    }, ensure_ascii=False, indent=2)


# ==================== Publisher 核心逻辑 ====================

class Publisher:
    def __init__(self, project_dir: str):
        self.project_dir = project_dir
        self.paths = _get_paths(project_dir)
        self.log = get_project_logger(project_dir, "Publisher")

    async def run(self) -> str:
        self.log.info("开始发布流程")
        plan = await self._plan_chapters()
        self.log.info(f"文章规划完成：《{plan.article_title}》，共 {len(plan.chapters)} 章")

        chapters = await self._write_chapters(plan)
        self.log.info("所有章节撰写完成")

        article_data = {
            "title": plan.article_title,
            "chapters": [c.model_dump() for c in chapters],
            "created_at": datetime.now().isoformat(),
        }
        write_json(self.paths["article"], article_data)

        report_path = await self._compile_report(article_data)
        final_path = await self._rename_report(report_path)
        self.log.info(f"报告发布完成：{final_path}")
        return final_path

    async def _plan_chapters(self) -> PublisherPlanResponse:
        shared_ctx = json.dumps(read_jsonl(self.paths["shared_memory"]), ensure_ascii=False, indent=2)
        researcher_list_ctx = json.dumps(read_jsonl(self.paths["researcher_list"]), ensure_ascii=False, indent=2)

        prompt = f"""你是一位专业的主编（Publisher），负责规划深度研究报告的结构。

共享研究记忆：
{shared_ctx}

Researcher任务列表：
{researcher_list_ctx}

请根据研究内容，规划文章的章节结构：
1. 为文章起一个准确的标题
2. 规划合理的章节（建议5-10章），章节顺序即为文章最终呈现顺序，注意研究的原始问题，不要过于偏离方向。
3. 大纲必须涵盖背景、技术现状、核心挑战、多维度对比、及未来趋势。
4. 为每个章节要有深度问题。例如：不要只写“现状”，要要求分析“导致现状的底层驱动因素”。
5. 为每个章节分配最合适的 Researcher（尽量安排研究过该主题的 Researcher 撰写对应章节）
6. 可以重复安排同一个 Researcher 撰写多个章节

注意：章节应覆盖所有重要研究结论，逻辑清晰，层次分明。
"""

#TODO 撰写部分非常重要，需要精调一下AI的提示词

        return await _call_gemini_with_retry(prompt, PublisherPlanResponse, self.log)

    async def _write_chapters(self, plan: PublisherPlanResponse) -> List[ChapterContent]:
        async def write_one_chapter(chapter: ChapterPlan) -> ChapterContent:
            context = _researcher_context(self.project_dir, chapter.researcher_id)
            shared_ctx = json.dumps(read_jsonl(self.paths["shared_memory"]), ensure_ascii=False, indent=2)

            prompt = f"""你是研究员 {chapter.researcher_id}，正在撰写深度研究报告的一个章节。

文章标题：{plan.article_title}
章节序号：{chapter.chapter_index}
章节标题：{chapter.title}
章节说明：{chapter.description}

你的完整研究上下文（包含研究记忆、任务列表、质疑记录）：
{context}

共享研究记忆（所有研究员的综合结论）：
{shared_ctx}

撰写要求：
1. 内容要详实、有据可查，引用具体的研究结论和数据
2. 逻辑清晰，语言专业流畅
3. 充分利用你的研究记忆中的数据和结论
4. 章节内容要与章节说明高度契合
5. 【重要】你的上下文中包含 doubt_records（质疑记录）：
   - 若你是"被质疑方"（role: 被质疑方）：在撰写时主动吸收质疑中指出的问题，对已被接受的质疑（accepted: true）在正文中予以修正或补充说明；对未被接受的质疑（accepted: false）可在正文中简要说明你的立场
   - 若你是"质疑方"（role: 质疑方）：在撰写时可引用你提出的质疑及对方的回答，增强论证的严谨性
   - 通过质疑-回答-评价的过程，使章节内容更加严谨、客观、有说服力
   如果你的研究结论曾被质疑且你给出了回答，可以将该辩证过程（即“为什么这个结论是可靠的”）内化到正文中，体现报告的严谨性。
"""
            result: ChapterContent = await _call_gemini_with_retry(prompt, ChapterContent, self.log)
            result.chapter_index = chapter.chapter_index
            result.title = chapter.title
            self.log.info(f"章节 {chapter.chapter_index}《{chapter.title}》撰写完成")
            return result

        chapters = await asyncio.gather(*[write_one_chapter(c) for c in plan.chapters])
        return sorted(chapters, key=lambda c: c.chapter_index)

    async def _compile_report(self, article_data: dict) -> str:
        """调用 Gemini 将 article.json 整理为完整 Markdown，利用其超长上下文和输出上限"""
        title = article_data.get("title", "深度研究报告")
        chapters_text = "\n\n".join([
            f"## {c['title']}\n\n{c['content']}"
            for c in article_data.get("chapters", [])
        ])

        # 读取质疑表，附加到整理上下文，让编辑了解各章节经过了哪些质疑与修正
        doubt_path = os.path.join(self.paths["base"], "doubt.jsonl")
        all_doubts = read_jsonl(doubt_path)
        doubt_summary = ""
        if all_doubts:
            accepted = [d for d in all_doubts if d.get("accepted") is True]
            rejected = [d for d in all_doubts if d.get("accepted") is False]
            doubt_summary = (
                f"\n\n---\n## 质疑与答辩记录摘要\n"
                f"本报告经过多轮研究员互相质疑与答辩，共产生 {len(all_doubts)} 条质疑，"
                f"其中 {len(accepted)} 条被接受（已在正文中修正），{len(rejected)} 条未被接受（研究员坚持原结论）。\n"
            )

        prompt = f"""你是一位专业的深度研究报告主编，负责将各章节内容整理为一篇格式规范、逻辑严谨的完整 Markdown 报告。

报告标题：{title}

各章节内容（按顺序）：
{chapters_text}
{doubt_summary}

整理要求：
1. 在报告最开头输出一级标题（# {title}）
2. 紧接标题后撰写"执行摘要"（200-400字），概括核心结论和研究价值
3. 保持原有章节结构，使用二级标题（##）标注各章节，尽可能保留各个章节的内容，不要删减内容。
4. 优化语言表达，消除重复内容，确保全文对同一技术或现象的称呼一致，使行文流畅自然、前后呼应
5. “在构建大纲时，请不仅考虑‘是什么’，更要要求探讨‘为什么’以及‘如果...会怎样’
6. 对各章节中因质疑而修正的内容，确保修正后的表述清晰准确
7. 文章是要争议性话题给出了平衡的视角，从多个方面发分析问题。
8. 在报告末尾添加"综合结论"章节（## 综合结论），结论是否具有前瞻性和行动建议，综合所有章节得出最终判断
9. 直接输出完整 Markdown 内容，不要输出任何解释性文字或代码块标记
"""

        markdown_content = f"# {title}\n\n{chapters_text}"
        for attempt in range(MAX_RETRIES):
            try:
                response = await asyncio.to_thread(
                    client.models.generate_content,
                    model=MODEL,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="text/plain",
                    ),
                )
                markdown_content = response.text
                self.log.info(f"Gemini 整理完成，报告长度 {len(markdown_content)} 字")
                break
            except Exception as e:
                self.log.warning(f"Gemini 整理调用失败（第 {attempt+1} 次）: {e}")
                if attempt == MAX_RETRIES - 1:
                    self.log.error(f"Gemini 整理最终失败，使用降级方案（直接拼接）: {e}")
                await asyncio.sleep(2 ** attempt)

        report_path = self.paths["report"]
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(markdown_content)

        return report_path

    async def _rename_report(self, report_path: str) -> str:
        with open(report_path, "r", encoding="utf-8") as f:
            content = f.read()[:3000]

        prompt = f"""根据以下研究报告的内容，为其生成一个简洁、准确的文件名（不含扩展名）。

报告内容（节选）：
{content}

要求：
- 文件名简洁，10-30个字符
- 可以使用中文或英文
- 不要包含特殊字符（/、\\、:、*、?、"、<、>、|）
- 能准确反映报告主题
"""
        result: ReportNameResponse = await _call_gemini_with_retry(prompt, ReportNameResponse, self.log)

        safe_name = result.report_name
        for ch in r'/\:*?"<>|':
            safe_name = safe_name.replace(ch, "_")
        safe_name = safe_name.strip()

        base_dir = os.path.dirname(report_path)
        new_path = os.path.join(base_dir, f"{safe_name}.md")
        os.rename(report_path, new_path)
        self.log.info(f"报告重命名为：{safe_name}.md")
        return new_path
