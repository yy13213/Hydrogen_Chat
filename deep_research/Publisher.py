"""
Publisher.py — 稿件发布者（主编）
负责：
1. 以 shared_memory 和 Researcher_list 为上下文，规划文章章节，分配给 Researcher 撰写
2. 并行调用所有 Researcher 的记忆，逐章节撰写，存入 article.json
3. 以 article.json 为上下文，调用 deepseek-chat 整理为 Markdown 格式
4. 对 research_report.md 重命名，输出最终结果
"""

import asyncio
import json
import os
from datetime import datetime
from typing import List

from google.genai import types
from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from gemini_client import client, MODEL
from utils import read_jsonl
from utils.file_lock import write_json

PROJECTS_DIR = os.getenv("PROJECTS_DIR", "projects")
MAX_RETRIES = 3

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "sk-87d7368283d2467888f2c94dddba0857")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")


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

async def _call_gemini_with_retry(prompt: str, response_schema, max_retries: int = MAX_RETRIES):
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
            if attempt == max_retries - 1:
                raise RuntimeError(f"Publisher Gemini 调用失败: {e}") from e
            await asyncio.sleep(1)


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
    base = os.path.join(PROJECTS_DIR, project_dir)
    shared = read_jsonl(os.path.join(base, "shared_memory.jsonl"))
    memory = read_jsonl(os.path.join(base, researcher_id, "memory.jsonl"))
    task_list = read_jsonl(os.path.join(base, researcher_id, "task_list.jsonl"))
    return json.dumps({
        "shared_memory": shared,
        "memory": memory,
        "task_list": task_list,
    }, ensure_ascii=False, indent=2)


# ==================== Publisher 核心逻辑 ====================

class Publisher:
    def __init__(self, project_dir: str):
        self.project_dir = project_dir
        self.paths = _get_paths(project_dir)

    async def run(self) -> str:
        plan = await self._plan_chapters()
        chapters = await self._write_chapters(plan)

        article_data = {
            "title": plan.article_title,
            "chapters": [c.model_dump() for c in chapters],
            "created_at": datetime.now().isoformat(),
        }
        write_json(self.paths["article"], article_data)

        report_path = await self._compile_report(article_data)
        final_path = await self._rename_report(report_path)
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
2. 规划合理的章节（建议5-10章），章节顺序即为文章最终呈现顺序
3. 为每个章节分配最合适的 Researcher（尽量安排研究过该主题的 Researcher 撰写对应章节）
4. 可以重复安排同一个 Researcher 撰写多个章节

注意：章节应覆盖所有重要研究结论，逻辑清晰，层次分明。
"""
        return await _call_gemini_with_retry(prompt, PublisherPlanResponse)

    async def _write_chapters(self, plan: PublisherPlanResponse) -> List[ChapterContent]:
        async def write_one_chapter(chapter: ChapterPlan) -> ChapterContent:
            context = _researcher_context(self.project_dir, chapter.researcher_id)
            shared_ctx = json.dumps(read_jsonl(self.paths["shared_memory"]), ensure_ascii=False, indent=2)

            prompt = f"""你是研究员 {chapter.researcher_id}，正在撰写深度研究报告的一个章节。

文章标题：{plan.article_title}
章节序号：{chapter.chapter_index}
章节标题：{chapter.title}
章节说明：{chapter.description}

你的研究记忆（包含相关研究结论）：
{context}

共享研究记忆：
{shared_ctx}

请根据你的研究记忆，撰写这一章节的完整内容：
1. 内容要详实、有据可查，引用具体的研究结论
2. 逻辑清晰，语言专业流畅
3. 充分利用你的研究记忆中的数据和结论
4. 章节内容要与章节说明高度契合
"""
            result: ChapterContent = await _call_gemini_with_retry(prompt, ChapterContent)
            result.chapter_index = chapter.chapter_index
            result.title = chapter.title
            return result

        chapters = await asyncio.gather(*[write_one_chapter(c) for c in plan.chapters])
        return sorted(chapters, key=lambda c: c.chapter_index)

    async def _compile_report(self, article_data: dict) -> str:
        """调用 deepseek-chat 将 article.json 整理为 Markdown"""
        deepseek_client = AsyncOpenAI(
            api_key=DEEPSEEK_API_KEY,
            base_url=DEEPSEEK_BASE_URL,
        )

        chapters_text = "\n\n".join([
            f"## {c['title']}\n\n{c['content']}"
            for c in article_data.get("chapters", [])
        ])

        messages = [
            {
                "role": "system",
                "content": (
                    "你是一位专业的文章编辑，负责将研究报告整理为格式规范的 Markdown 文档。"
                    "要求：\n"
                    "1. 保持原有章节结构，使用标准 Markdown 标题层级\n"
                    "2. 优化语言表达，使文章流畅自然\n"
                    "3. 添加适当的段落分隔和格式\n"
                    "4. 在文章开头添加摘要\n"
                    "5. 在文章结尾添加结论\n"
                    "6. 直接输出 Markdown 内容，不要有任何额外说明"
                ),
            },
            {
                "role": "user",
                "content": f"请将以下研究报告整理为完整的 Markdown 格式文档：\n\n标题：{article_data.get('title', '深度研究报告')}\n\n{chapters_text}",
            },
        ]

        markdown_content = f"# {article_data.get('title', '深度研究报告')}\n\n{chapters_text}"
        for attempt in range(MAX_RETRIES):
            try:
                response = await deepseek_client.chat.completions.create(
                    model="deepseek-chat",
                    messages=messages,
                    stream=False,
                )
                markdown_content = response.choices[0].message.content
                break
            except Exception as e:
                if attempt == MAX_RETRIES - 1:
                    print(f"[Publisher] deepseek-chat 调用失败，使用降级方案: {e}")
                await asyncio.sleep(1)

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
        result: ReportNameResponse = await _call_gemini_with_retry(prompt, ReportNameResponse)

        safe_name = result.report_name
        for ch in r'/\:*?"<>|':
            safe_name = safe_name.replace(ch, "_")
        safe_name = safe_name.strip()

        base_dir = os.path.dirname(report_path)
        new_path = os.path.join(base_dir, f"{safe_name}.md")
        os.rename(report_path, new_path)
        return new_path
