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


class MemoryControlInstructions(BaseModel):
    instructions: list[dict] = Field(description="操作指令列表，每条为 delete 或 alter 操作")
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
    task_index = {}
    for si, rec in enumerate(records):
        if rec.get("type") == "sub_research":
            for ti, task in enumerate(rec.get("tasks", [])):
                task_index[task.get("task_id")] = (si, ti)

    ids_to_delete = set()
    for instr in instructions:
        op = instr.get("op")
        if op == "delete":
            task_id = instr.get("task_id")
            if task_id in task_index:
                ids_to_delete.add(task_id)
        elif op == "alter":
            target_id = instr.get("target_task_id")
            source_ids = instr.get("source_task_ids", [])
            if target_id in task_index:
                si, ti = task_index[target_id]
                records[si]["tasks"][ti]["action"] = instr.get("merged_action", "")
                records[si]["tasks"][ti]["conclusion"] = instr.get("merged_conclusion", "")
                records[si]["tasks"][ti]["credibility"] = instr.get("merged_credibility", 0)
            for sid in source_ids:
                ids_to_delete.add(sid)

    for rec in records:
        if rec.get("type") == "sub_research":
            rec["tasks"] = [t for t in rec.get("tasks", []) if t.get("task_id") not in ids_to_delete]
    return records


def _apply_instructions_to_memory(records: list, instructions: list) -> list:
    task_index = {rec.get("task_id"): i for i, rec in enumerate(records)}
    ids_to_delete = set()
    for instr in instructions:
        op = instr.get("op")
        if op == "delete":
            task_id = instr.get("task_id")
            if task_id in task_index:
                ids_to_delete.add(task_id)
        elif op == "alter":
            target_id = instr.get("target_task_id")
            source_ids = instr.get("source_task_ids", [])
            if target_id in task_index:
                idx = task_index[target_id]
                records[idx]["A"] = instr.get("merged_action", "")
                records[idx]["R"] = instr.get("merged_conclusion", "")
                records[idx]["C"] = instr.get("merged_credibility", 0)
            for sid in source_ids:
                ids_to_delete.add(sid)
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
- delete：删除无效信息、重复信息、价值低的信息（op: "delete", task_id: "xxx"）
- alter：将多条同类信息合并为一条（op: "alter", target_task_id: "xxx", source_task_ids: ["yyy","zzz"], merged_action: "...", merged_conclusion: "...", merged_credibility: 数值）

注意：
- alter 操作中 source_task_ids 不能包含 target_task_id
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
- delete：删除无效信息、重复信息（op: "delete", task_id: "xxx"）
- alter：将多条同类记忆合并（op: "alter", target_task_id: "xxx", source_task_ids: ["yyy"], merged_action: "...", merged_conclusion: "...", merged_credibility: 数值）
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
