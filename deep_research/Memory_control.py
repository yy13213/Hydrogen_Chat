"""
Memory_control.py — 记忆管理者
负责：
1. 当 shared_memory.jsonl 超过 50000 字时触发，批量压缩
2. 当某个 memory.jsonl 超过 100000 字时触发，批量压缩
3. 通过 delete（删除无效/冗余信息）和 alter（合并同类信息）操作压缩记忆
"""

import asyncio
import json
import os

from google.genai import types
from pydantic import BaseModel, Field

from gemini_client import client, MODEL, PROJECTS_DIR
from logger import get_project_logger
from utils import read_jsonl
from utils.file_lock import write_jsonl_all, get_file_size_chars

MAX_RETRIES = 3
SHARED_MEMORY_THRESHOLD = 50000
RESEARCHER_MEMORY_THRESHOLD = 100000


class MemoryInstruction(BaseModel):
    op: str = Field(description="操作类型：delete 或 alter")
    id: str = Field(description="要操作的记录ID")
    new_content: str = Field(default="", description="alter 操作时的新内容，delete 时为空")


class MemoryControlInstructions(BaseModel):
    instructions: list[MemoryInstruction] = Field(description="操作指令列表，每条为 delete 或 alter 操作")
    summary: str = Field(description="本次压缩操作的总结")


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
            log.warning(f"Memory_control 调用失败（第 {attempt+1} 次）: {e}")
            if attempt == max_retries - 1:
                log.error(f"Memory_control 调用最终失败: {e}", exc_info=True)
                raise RuntimeError(f"Memory_control 调用失败: {e}") from e
            await asyncio.sleep(2 ** attempt)


def _apply_instructions_to_shared_memory(records: list, instructions: list) -> list:
    """instructions 为 MemoryInstruction 对象列表"""
    task_index = {}
    for si, rec in enumerate(records):
        if rec.get("type") == "sub_research":
            for ti, task in enumerate(rec.get("tasks", [])):
                task_index[task.get("task_id")] = (si, ti)

    ids_to_delete = set()
    for instr in instructions:
        op = instr.op if hasattr(instr, "op") else instr.get("op", "")
        rec_id = instr.id if hasattr(instr, "id") else instr.get("id", "")
        new_content = instr.new_content if hasattr(instr, "new_content") else instr.get("new_content", "")

        if op == "delete":
            if rec_id in task_index:
                ids_to_delete.add(rec_id)
        elif op == "alter":
            if rec_id in task_index:
                si, ti = task_index[rec_id]
                records[si]["tasks"][ti]["conclusion"] = new_content

    for rec in records:
        if rec.get("type") == "sub_research":
            rec["tasks"] = [t for t in rec.get("tasks", []) if t.get("task_id") not in ids_to_delete]
    return records


def _apply_instructions_to_memory(records: list, instructions: list) -> list:
    """instructions 为 MemoryInstruction 对象列表"""
    task_index = {rec.get("task_id"): i for i, rec in enumerate(records)}
    ids_to_delete = set()
    for instr in instructions:
        op = instr.op if hasattr(instr, "op") else instr.get("op", "")
        rec_id = instr.id if hasattr(instr, "id") else instr.get("id", "")
        new_content = instr.new_content if hasattr(instr, "new_content") else instr.get("new_content", "")

        if op == "delete":
            if rec_id in task_index:
                ids_to_delete.add(rec_id)
        elif op == "alter":
            if rec_id in task_index:
                idx = task_index[rec_id]
                records[idx]["R"] = new_content
    return [r for r in records if r.get("task_id") not in ids_to_delete]


async def compress_shared_memory(project_dir: str) -> bool:
    log = get_project_logger(project_dir, "Memory_control")
    path = os.path.join(PROJECTS_DIR, project_dir, "shared_memory.jsonl")
    size = get_file_size_chars(path)
    if size < SHARED_MEMORY_THRESHOLD:
        return False

    log.info(f"shared_memory 超过阈值（{size} 字），开始压缩")
    records = read_jsonl(path)
    all_tasks = []
    for rec in records:
        if rec.get("type") == "sub_research":
            for task in rec.get("tasks", []):
                all_tasks.append({
                    "task_id": task.get("task_id"),
                    "sub_research_id": rec.get("sub_research_id"),
                    "background": rec.get("background", ""),
                    "action": task.get("action", ""),
                    "conclusion": task.get("conclusion", ""),
                    "credibility": task.get("credibility", 0),
                })
    if not all_tasks:
        return False

    prompt = f"""你是记忆管理者（Memory_control），负责压缩研究记忆。

当前 shared_memory.jsonl 已超过 50000 字，需要压缩。

所有任务数据：
{json.dumps(all_tasks, ensure_ascii=False, indent=2)}

请批量生成压缩指令（尽可能多地生成指令以高效压缩）：
- delete：删除无效/重复/低价值信息，字段：op="delete", id=task_id, new_content=""
- alter：将多条同类信息合并，保留一条并更新其 conclusion，字段：op="alter", id=保留的task_id, new_content="合并后的结论"

注意：
- 每条指令仅操作一个 id
- 尽量保留高可信度的信息
"""
    result: MemoryControlInstructions = await _call_gemini_with_retry(prompt, MemoryControlInstructions, log)
    updated_records = _apply_instructions_to_shared_memory(records, result.instructions)
    write_jsonl_all(path, updated_records)
    log.info(f"shared_memory 压缩完成：{result.summary}")
    return True


async def compress_researcher_memory(project_dir: str, researcher_id: str) -> bool:
    log = get_project_logger(project_dir, "Memory_control")
    path = os.path.join(PROJECTS_DIR, project_dir, researcher_id, "memory.jsonl")
    size = get_file_size_chars(path)
    if size < RESEARCHER_MEMORY_THRESHOLD:
        return False

    log.info(f"{researcher_id} memory 超过阈值（{size} 字），开始压缩")
    records = read_jsonl(path)
    if not records:
        return False

    prompt = f"""你是记忆管理者（Memory_control），负责压缩研究员记忆。

{researcher_id} 的 memory.jsonl 已超过 100000 字，需要压缩。

所有 STAR 记忆体：
{json.dumps(records, ensure_ascii=False, indent=2)}

请批量生成压缩指令：
- delete：删除无效/重复信息，字段：op="delete", id=task_id, new_content=""
- alter：将多条同类记忆合并，保留一条并更新其结论（R字段），字段：op="alter", id=保留的task_id, new_content="合并后的结论"

注意：每条指令仅操作一个 id。
"""
    result: MemoryControlInstructions = await _call_gemini_with_retry(prompt, MemoryControlInstructions, log)
    updated_records = _apply_instructions_to_memory(records, result.instructions)
    write_jsonl_all(path, updated_records)
    log.info(f"{researcher_id} memory 压缩完成：{result.summary}")
    return True


async def check_and_compress(project_dir: str, researcher_id: str = None) -> None:
    await compress_shared_memory(project_dir)
    if researcher_id:
        await compress_researcher_memory(project_dir, researcher_id)
