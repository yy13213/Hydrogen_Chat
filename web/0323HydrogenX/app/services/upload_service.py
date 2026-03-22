import hashlib
import mimetypes
import re
import uuid
from pathlib import Path

from flask import current_app
from werkzeug.datastructures import FileStorage

from ..models import UploadedFile

TEXT_EXTENSIONS = {".txt", ".md", ".csv", ".json", ".py", ".js", ".html", ".css", ".yml", ".yaml", ".xml", ".log"}


class UploadService:
    def user_upload_dir(self, user_id: int) -> Path:
        root = Path(current_app.root_path).parent / current_app.config["OPENCLAW_UPLOADS_DIR"]
        return root / f"user_{user_id}"

    def upload_abspath(self, upload: UploadedFile) -> Path:
        root = Path(current_app.root_path).parent / current_app.config["OPENCLAW_UPLOADS_DIR"]
        return root / upload.relative_path

    def sanitize_filename(self, value: str) -> str:
        value = Path(value).name
        value = re.sub(r"[^a-zA-Z0-9._-]+", "_", value).strip("._")
        return value[:180] or "upload"

    def save_upload(self, file: FileStorage, user_id: int) -> UploadedFile:
        filename = self.sanitize_filename(file.filename or "upload")
        binary = file.read()
        max_bytes = int(current_app.config["OPENCLAW_MAX_UPLOAD_MB"]) * 1024 * 1024
        if not binary:
            raise ValueError("上传文件不能为空。")
        if len(binary) > max_bytes:
            raise ValueError(f"单个文件不能超过 {current_app.config['OPENCLAW_MAX_UPLOAD_MB']} MB。")

        user_dir = self.user_upload_dir(user_id)
        user_dir.mkdir(parents=True, exist_ok=True)

        storage_name = f"{uuid.uuid4().hex}_{filename}"
        relative_path = f"user_{user_id}/{storage_name}"
        file_path = user_dir / storage_name
        file_path.write_bytes(binary)

        mime_type = file.mimetype or mimetypes.guess_type(filename)[0] or "application/octet-stream"
        sha256 = hashlib.sha256(binary).hexdigest()
        text_excerpt = self.extract_text_excerpt(filename, binary)

        return UploadedFile(
            user_id=user_id,
            filename=filename,
            storage_name=storage_name,
            relative_path=relative_path,
            mime_type=mime_type,
            size_bytes=len(binary),
            sha256=sha256,
            text_excerpt=text_excerpt,
        )

    def extract_text_excerpt(self, filename: str, binary: bytes) -> str | None:
        suffix = Path(filename).suffix.lower()
        if suffix not in TEXT_EXTENSIONS:
            return None
        try:
            text = binary.decode("utf-8", errors="replace")
        except Exception:
            return None
        return text[:8000]

    def build_prompt_context(self, uploads: list[UploadedFile]) -> str:
        if not uploads:
            return ""

        parts = [
            "以下是用户本次随任务上传的文件，请结合这些文件内容回答。"
        ]
        for upload in uploads:
            header = f"[Uploaded File #{upload.id}] {upload.filename} ({upload.mime_type or 'application/octet-stream'}, {upload.size_bytes or 0} bytes)"
            parts.append(header)
            if upload.text_excerpt:
                parts.append("文件内容摘录：")
                parts.append(upload.text_excerpt)
            else:
                parts.append("该文件为非纯文本或未提取正文，请至少参考其文件名、类型与用途。")
            parts.append("")

        return "\n".join(parts).strip()
