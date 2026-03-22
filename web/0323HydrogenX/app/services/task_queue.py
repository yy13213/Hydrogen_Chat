import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from queue import Queue

from flask import current_app

from ..extensions import db
from ..models import ChatTask
from .artifact_service import ArtifactService
from .openclaw_client import OpenClawClient
from .upload_service import UploadService


class TaskQueue:
    def __init__(self):
        self.app = None
        self.queue = Queue()
        self.executor = None
        self.dispatcher = None
        self._started = False

    def init_app(self, app):
        self.app = app
        self.executor = ThreadPoolExecutor(
            max_workers=app.config["OPENCLAW_MAX_CONCURRENT"],
            thread_name_prefix="openclaw-worker",
        )

    def start(self):
        if self._started:
            return

        self._started = True
        self.dispatcher = threading.Thread(
            target=self._dispatch_loop,
            name="openclaw-dispatcher",
            daemon=True,
        )
        self.dispatcher.start()

    def enqueue(self, task_id: int):
        self.queue.put(task_id)

    def _dispatch_loop(self):
        while True:
            task_id = self.queue.get()
            try:
                self.executor.submit(self._process_task, task_id)
            finally:
                self.queue.task_done()

    def _process_task(self, task_id: int):
        with self.app.app_context():
            task = db.session.get(ChatTask, task_id)
            if not task or task.status != "queued":
                return

            task.status = "running"
            task.error_message = None
            task.started_at = datetime.utcnow()
            db.session.commit()

            try:
                upload_context = UploadService().build_prompt_context(list(task.uploads))
                effective_prompt = task.prompt
                if upload_context:
                    effective_prompt = f"{task.prompt}\n\n{upload_context}"

                result = OpenClawClient().call(
                    prompt=effective_prompt,
                    user_id=task.user_id,
                    agent_id=task.agent_id,
                    session_key=task.session_key,
                )
                task.status = "completed"
                task.response_text = result["output_text"]
                task.raw_response_json = json.dumps(result["raw"], ensure_ascii=False, indent=2)

                artifacts = ArtifactService().persist_from_response(
                    task=task,
                    raw_response=result["raw"],
                    manifest=result.get("artifact_manifest"),
                    output_text=result.get("output_text"),
                )
                for artifact in artifacts:
                    db.session.add(artifact)
            except Exception as exc:
                current_app.logger.exception(
                    "Task %s failed (agent=%s, session_key=%s)",
                    task_id,
                    task.agent_id,
                    task.session_key,
                )
                task.status = "failed"
                task.error_message = str(exc)
            finally:
                task.finished_at = datetime.utcnow()
                db.session.commit()


task_queue = TaskQueue()
