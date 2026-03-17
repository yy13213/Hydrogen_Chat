"""
Planner.py — 规划者
负责：
1. 研究开始时建立项目目录，重写问题，拆分子研究，并行启动 Researcher
2. 每当某个 Researcher 完成工作时激活，继续规划新的子研究
3. 当某个 Researcher 超时（>300s）时激活，拆分其工作给空闲 Researcher
4. 最多串行 6 次研究，第 4 次起提示倒计时
5. 所有子研究完成后调用 Doubter
"""

import asyncio
import os
import shutil
from datetime import datetime

from google.genai import types
from pydantic import BaseModel, Field

from gemini_client import client, MODEL, PROJECTS_DIR
from logger import get_project_logger
from utils import generate_id, read_jsonl, write_jsonl_append, update_jsonl_record
from utils.file_lock import write_jsonl_all

MAX_RESEARCHERS = 5
MAX_SERIAL_ROUNDS = 6
RESEARCHER_TIMEOUT = 300


# ==================== 结构化返回模型 ====================

class SubResearch(BaseModel):
    sub_research_id: str = Field(description="子研究ID（由框架生成，留空即可）")
    researcher_id: str = Field(description="分配的Researcher编号，如 Researcher1")
    background: str = Field(description="子研究背景")
    goal: str = Field(description="子研究目标")


class PlannerInitResponse(BaseModel):
    rewritten_question: str = Field(description="重写后的研究问题")
    sub_researches: list[SubResearch] = Field(description="初始子研究列表")


class PlannerContinueResponse(BaseModel):
    continue_research: bool = Field(description="是否需要继续研究")
    sub_researches: list[SubResearch] = Field(description="新的子研究列表，若不需要继续则为空列表")
    reason: str = Field(description="决策理由")


class SplitTask(BaseModel):
    researcher_id: str = Field(description="目标Researcher编号，如 Researcher2")
    background: str = Field(description="任务背景")
    goal: str = Field(description="任务目标")


class PlannerTimeoutResponse(BaseModel):
    split_tasks: list[SplitTask] = Field(description="拆分给空闲Researcher的任务列表")
    reason: str = Field(description="拆分理由")


# ==================== 辅助函数 ====================

def _get_project_paths(project_dir: str) -> dict:
    base = os.path.join(PROJECTS_DIR, project_dir)
    return {
        "base": base,
        "shared_memory": os.path.join(base, "shared_memory.jsonl"),
        "researcher_list": os.path.join(base, "Researcher_list.jsonl"),
        "article": os.path.join(base, "article.json"),
        "doubt": os.path.join(base, "doubt.jsonl"),
        "report": os.path.join(base, "research_report.md"),
        "finalized_flag": os.path.join(base, ".finalized"),
    }


def _researcher_paths(project_dir: str, researcher_id: str) -> dict:
    d = os.path.join(PROJECTS_DIR, project_dir, researcher_id)
    return {
        "dir": d,
        "task_list": os.path.join(d, "task_list.jsonl"),
        "memory": os.path.join(d, "memory.jsonl"),
    }


async def _call_gemini_with_retry(prompt: str, response_schema, project_dir: str, max_retries: int = 3):
    import json
    log = get_project_logger(project_dir, "Planner")
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
                log.error(f"Gemini 调用最终失败（已重试 {max_retries} 次）: {e}")
                raise RuntimeError(f"Gemini 调用失败（已重试 {max_retries} 次）: {e}") from e
            await asyncio.sleep(2 ** attempt)


# ==================== Planner 核心逻辑 ====================

class Planner:
    def __init__(self, project_dir: str):
        self.project_dir = project_dir
        self.paths = _get_project_paths(project_dir)
        self.serial_round = 0
        self.log = get_project_logger(project_dir, "Planner")

    @classmethod
    def create_project(cls) -> "Planner":
        """创建新项目目录，返回 Planner 实例"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        project_dir = timestamp
        base = os.path.join(PROJECTS_DIR, project_dir)
        os.makedirs(base, exist_ok=True)
        for i in range(1, MAX_RESEARCHERS + 1):
            os.makedirs(os.path.join(base, f"Researcher{i}"), exist_ok=True)
        return cls(project_dir)

    async def init_research(self, user_question: str) -> str:
        """
        研究初始化：重写问题，拆分子研究，并行启动所有 Researcher。
        返回项目目录名。
        """
        self.serial_round = 1
        paths = self.paths
        self.log.info(f"开始研究初始化，问题：{user_question[:80]}...")

        write_jsonl_append(paths["shared_memory"], {
            "type": "init",
            "user_question": user_question,
            "rewritten_question": "",
        })

        prompt = f"""你是一位深度研究规划者。
用户问题：{user_question}

请完成以下任务：
1. 对用户问题进行重写，使其更清晰、更适合深度研究
2. 将研究拆分为多个可并行的子研究（最多5个），为每个子研究分配一个Researcher（Researcher1-Researcher5）
3. 每个子研究需要有明确的背景和目标

注意：
- 子研究ID字段留空，系统会自动生成
- 尽量让子研究相互独立，可以并行进行
- 每个子研究要有清晰的研究边界
- “在构建任务时，请不仅考虑‘是什么’，更要要求探讨‘为什么’以及‘如果...会怎样’
- 深挖问题的本质，逃避了复杂矛盾，挑战探讨问题的根源，而非停留在表面。
"""
        result: PlannerInitResponse = await _call_gemini_with_retry(
            prompt, PlannerInitResponse, self.project_dir
        )
        self.log.info(f"问题重写完成：{result.rewritten_question[:80]}...")
        self.log.info(f"规划 {len(result.sub_researches)} 个子研究")

        # 更新 shared_memory 中的重写问题
        records = read_jsonl(paths["shared_memory"])
        for rec in records:
            if rec.get("type") == "init":
                rec["rewritten_question"] = result.rewritten_question
        write_jsonl_all(paths["shared_memory"], records)

        # 写入子研究记录，收集启动参数
        sub_research_tasks = []
        for sub in result.sub_researches:
            sub_id = generate_id()
            now = datetime.now().isoformat()
            researcher_id = sub.researcher_id

            write_jsonl_append(paths["researcher_list"], {
                "sub_research_id": sub_id,
                "researcher_id": researcher_id,
                "background": sub.background,
                "goal": sub.goal,
                "status": "pending",
                "start_time": now,
                "end_time": None,
            })

            write_jsonl_append(paths["shared_memory"], {
                "type": "sub_research",
                "sub_research_id": sub_id,
                "researcher_id": researcher_id,
                "background": sub.background,
                "goal": sub.goal,
                "tasks": [],
            })

            r_paths = _researcher_paths(self.project_dir, researcher_id)
            os.makedirs(r_paths["dir"], exist_ok=True)

            sub_research_tasks.append((sub_id, researcher_id, sub.background, sub.goal))
            self.log.info(f"子研究 [{sub_id}] → {researcher_id}：{sub.goal[:60]}...")

        # ✅ 核心修复：并行启动所有 Researcher
        self.log.info(f"并行启动 {len(sub_research_tasks)} 个 Researcher...")
        await self._launch_researchers(sub_research_tasks)

        return self.project_dir

    async def _launch_researchers(self, sub_research_tasks: list) -> None:
        """并行启动多个 Researcher"""
        from Researcher import Researcher

        async def launch_one(sub_id: str, researcher_id: str, background: str, goal: str):
            try:
                self.log.info(f"启动 {researcher_id}，子研究 [{sub_id}]")
                researcher = Researcher(self.project_dir, researcher_id)
                await researcher.start(sub_id, background, goal)
            except Exception as e:
                self.log.error(f"{researcher_id} 执行失败 [{sub_id}]: {e}", exc_info=True)

        await asyncio.gather(*[
            launch_one(sub_id, r_id, bg, goal)
            for sub_id, r_id, bg, goal in sub_research_tasks
        ])

    async def on_researcher_complete(self, researcher_id: str) -> bool:
        """某个 Researcher 完成后激活 Planner。返回 True 表示继续研究。"""
        self.serial_round += 1
        paths = self.paths
        self.log.info(f"{researcher_id} 完成，当前第 {self.serial_round} 轮")

        # 若已进入收尾阶段，直接跳过（防止并发重复触发）
        if os.path.exists(paths["finalized_flag"]):
            self.log.info(f"{researcher_id} 完成通知到达，但项目已进入收尾阶段，跳过")
            return False

        update_jsonl_record(
            paths["researcher_list"],
            lambda r: r.get("researcher_id") == researcher_id and r.get("status") == "running",
            lambda r: {**r, "status": "completed", "end_time": datetime.now().isoformat()},
        )

        if self.serial_round >= MAX_SERIAL_ROUNDS:
            self.log.info(f"已达最大串行轮次 {MAX_SERIAL_ROUNDS}，进入收尾阶段")
            return await self._finalize_research()

        countdown_hint = ""
        if self.serial_round >= 4:
            remaining = MAX_SERIAL_ROUNDS - self.serial_round
            countdown_hint = f"\n\n⚠️ 倒计时提示：已进行 {self.serial_round} 轮研究，请在 {remaining} 轮内完成剩余研究！"

        import json
        shared_ctx = json.dumps(read_jsonl(paths["shared_memory"]), ensure_ascii=False, indent=2)
        researcher_list_ctx = json.dumps(read_jsonl(paths["researcher_list"]), ensure_ascii=False, indent=2)

        all_records = read_jsonl(paths["researcher_list"])
        busy_researchers = {r["researcher_id"] for r in all_records if r.get("status") == "running"}
        all_researchers = {f"Researcher{i}" for i in range(1, MAX_RESEARCHERS + 1)}
        idle_researchers = all_researchers - busy_researchers

        prompt = f"""你是一位深度研究规划者。
当前 {researcher_id} 已完成工作。

共享记忆（已完成的研究内容）：
{shared_ctx}

Researcher任务列表：
{researcher_list_ctx}

当前空闲的Researcher：{', '.join(idle_researchers) if idle_researchers else '无'}
{countdown_hint}

请分析当前研究进展，决定：
1. 是否需要继续研究（continue_research: true/false）
2. 如果需要，为空闲的Researcher安排新的子研究任务
3. 注意：每个Researcher有自己的记忆，建议为同一Researcher分配相关工作；新工作尽量给未参与过的Researcher
4. 如果所有研究目标已达成，返回 continue_research: false
5. “在构建任务时，请不仅考虑‘是什么’，更要要求探讨‘为什么’以及‘如果...会怎样’
6. 深挖问题的本质，逃避了复杂矛盾，挑战探讨问题的根源，而非停留在表面。

注意：Planner无需规划撰写报告，只需把研究所需要的所有结论得出即可。
"""
        result: PlannerContinueResponse = await _call_gemini_with_retry(
            prompt, PlannerContinueResponse, self.project_dir
        )
        self.log.info(f"Planner 决策：continue={result.continue_research}，理由：{result.reason[:80]}")

        if not result.continue_research:
            # 再次检查，防止 AI 决策期间另一个协程已触发收尾
            if os.path.exists(paths["finalized_flag"]):
                self.log.info("AI 决策期间项目已进入收尾阶段，跳过重复触发")
                return False
            return await self._finalize_research()

        # 写入新子研究并并行启动
        sub_research_tasks = []
        for sub in result.sub_researches:
            sub_id = generate_id()
            now = datetime.now().isoformat()
            r_id = sub.researcher_id

            write_jsonl_append(paths["researcher_list"], {
                "sub_research_id": sub_id,
                "researcher_id": r_id,
                "background": sub.background,
                "goal": sub.goal,
                "status": "pending",
                "start_time": now,
                "end_time": None,
            })

            write_jsonl_append(paths["shared_memory"], {
                "type": "sub_research",
                "sub_research_id": sub_id,
                "researcher_id": r_id,
                "background": sub.background,
                "goal": sub.goal,
                "tasks": [],
            })

            r_paths = _researcher_paths(self.project_dir, r_id)
            os.makedirs(r_paths["dir"], exist_ok=True)
            sub_research_tasks.append((sub_id, r_id, sub.background, sub.goal))
            self.log.info(f"新子研究 [{sub_id}] → {r_id}：{sub.goal[:60]}...")

        await self._launch_researchers(sub_research_tasks)
        return True

    async def on_researcher_timeout(self, researcher_id: str) -> None:
        """某个 Researcher 超时（>300s）时激活，拆分其工作给空闲 Researcher"""
        import json
        paths = self.paths
        self.log.warning(f"{researcher_id} 超时，开始拆分工作")

        r_paths = _researcher_paths(self.project_dir, researcher_id)
        memory_records = read_jsonl(r_paths["memory"])
        task_records = read_jsonl(r_paths["task_list"])
        pending_tasks = [t for t in task_records if t.get("status") == "pending"]

        all_records = read_jsonl(paths["researcher_list"])
        busy_researchers = {r["researcher_id"] for r in all_records if r.get("status") == "running"}
        all_researchers = {f"Researcher{i}" for i in range(1, MAX_RESEARCHERS + 1)}
        idle_researchers = list(all_researchers - busy_researchers - {researcher_id})

        if not idle_researchers or not pending_tasks:
            self.log.warning(f"无空闲 Researcher 或无待处理任务，跳过超时拆分")
            return

        prompt = f"""你是一位深度研究规划者。
{researcher_id} 已运行超过300秒，需要拆分其剩余工作。

{researcher_id} 的记忆：
{json.dumps(memory_records, ensure_ascii=False, indent=2)}

{researcher_id} 未完成的任务：
{json.dumps(pending_tasks, ensure_ascii=False, indent=2)}

当前空闲的Researcher：{', '.join(idle_researchers)}

请将未完成的任务合理拆分给空闲的Researcher，结构化返回拆分方案。
每项包含：researcher_id（目标Researcher）、background（任务背景）、goal（任务目标）。
"""
        result: PlannerTimeoutResponse = await _call_gemini_with_retry(
            prompt, PlannerTimeoutResponse, self.project_dir
        )

        sub_research_tasks = []
        for task in result.split_tasks:
            target_r = task.researcher_id
            if not target_r:
                continue

            sub_id = generate_id()
            now = datetime.now().isoformat()

            write_jsonl_append(paths["researcher_list"], {
                "sub_research_id": sub_id,
                "researcher_id": target_r,
                "background": task.background,
                "goal": task.goal,
                "status": "pending",
                "start_time": now,
                "end_time": None,
            })

            write_jsonl_append(paths["shared_memory"], {
                "type": "sub_research",
                "sub_research_id": sub_id,
                "researcher_id": target_r,
                "background": task.background,
                "goal": task.goal,
                "tasks": [],
            })

            target_r_paths = _researcher_paths(self.project_dir, target_r)
            os.makedirs(target_r_paths["dir"], exist_ok=True)
            if os.path.exists(r_paths["memory"]):
                shutil.copy2(r_paths["memory"], target_r_paths["memory"])

            sub_research_tasks.append((sub_id, target_r, task.background, task.goal))
            self.log.info(f"超时拆分：[{sub_id}] → {target_r}")

        await self._launch_researchers(sub_research_tasks)

    async def _finalize_research(self) -> bool:
        """所有研究完成，调用 Doubter（幂等保护：整个项目生命周期只执行一次）"""
        from filelock import FileLock

        flag = self.paths["finalized_flag"]
        lock_path = flag + ".lock"

        # 用文件锁保证原子性：只有第一个抢到锁且 flag 不存在的协程才能继续
        with FileLock(lock_path, timeout=5):
            if os.path.exists(flag):
                self.log.info("_finalize_research 已由其他协程触发，跳过重复执行")
                return False
            # 写入标志位，后续所有并发调用都会跳过
            with open(flag, "w") as f:
                f.write("finalized")

        self.log.info("所有子研究完成，调用 Doubter 进行质疑验证")
        from Doubter import Doubter
        doubter = Doubter(self.project_dir)
        await doubter.run()
        return False
