import base64
import hashlib
import io
import json
import mimetypes
import re
import uuid
import zipfile
from pathlib import Path
from urllib.parse import urlparse

import requests
from flask import current_app

from ..models import GeneratedArtifact


ARTIFACT_URL_KEYS = ("url", "download_url", "file_url", "image_url", "href")
ARTIFACT_DATA_KEYS = ("data", "content_base64", "base64_data", "b64_json", "bytes_base64")
ARTIFACT_FILENAME_KEYS = ("filename", "file_name", "name", "title")
ARTIFACT_MIME_KEYS = ("mime_type", "content_type", "media_type")
ARTIFACT_KIND_TYPES = {
    "image": "image",
    "output_image": "image",
    "image_file": "image",
    "file": "file",
    "output_file": "file",
    "document": "file",
    "zip": "archive",
    "archive": "archive",
}
SKIP_URL_PREFIXES = ("data:", "sandbox:/", "file://")


class ArtifactService:
    def persist_from_response(self, task, raw_response: dict, manifest: dict | None = None, output_text: str | None = None) -> list[GeneratedArtifact]:
        items = []

        if isinstance(manifest, dict):
            manifest_items = manifest.get("artifacts") or manifest.get("files") or []
            if isinstance(manifest_items, list):
                items.extend([self.normalize_manifest_item(x) for x in manifest_items if isinstance(x, dict)])

        items.extend(self.extract_artifact_candidates(raw_response))
        items.extend(self.infer_text_artifacts_from_prompt_and_response(
            prompt=task.prompt,
            output_text=output_text or "",
        ))

        saved = []
        seen = set()
        for index, item in enumerate(items, start=1):
            if not item:
                continue
            signature = json.dumps(item, sort_keys=True, ensure_ascii=False, default=str)
            if signature in seen:
                continue
            seen.add(signature)
            try:
                artifact = self.persist_candidate(task=task, candidate=item, index=index)
                if artifact:
                    saved.append(artifact)
            except Exception:
                current_app.logger.exception(
                    "Failed to persist artifact for task=%s user=%s candidate=%s",
                    task.id,
                    task.user_id,
                    item,
                )
        return saved

    def normalize_manifest_item(self, node: dict) -> dict:
        return {
            "type": str(node.get("kind") or node.get("type") or "file").strip().lower(),
            "url": (node.get("url") or node.get("download_url") or "").strip() or None,
            "data": (node.get("content_base64") or node.get("data") or "").strip() or None,
            "filename": (node.get("filename") or node.get("name") or "").strip() or None,
            "mime_type": (node.get("mime_type") or node.get("content_type") or "").strip() or None,
        }

    def infer_text_artifacts_from_prompt_and_response(self, prompt: str, output_text: str) -> list[dict]:
        prompt = (prompt or "").strip()
        output_text = (output_text or "").strip()
        if not prompt:
            return []

        # multi-file -> zip fallback
        zip_item = self.infer_zip_bundle_from_prompt_and_response(prompt, output_text)
        if zip_item:
            return [zip_item]

        # single small text file fallback
        filename = self.extract_filename(prompt) or self.extract_filename(output_text)
        if not filename:
            return []

        filename_lower = filename.lower()
        if not filename_lower.endswith((".txt", ".md", ".csv", ".json", ".py", ".js", ".html", ".css")):
            return []

        content = self.extract_content_from_prompt(prompt)
        if content is None:
            content = self.extract_content_from_response(output_text)

        if content is None:
            return []

        mime_type = self.choose_mime_type({"mime_type": None}, filename)
        encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
        if filename_lower.endswith(".json"):
            mime_type = "application/json"
        elif filename_lower.endswith(".csv"):
            mime_type = "text/csv"
        elif filename_lower.endswith(".md"):
            mime_type = "text/markdown"
        elif filename_lower.endswith(".html"):
            mime_type = "text/html"
        elif filename_lower.endswith(".css"):
            mime_type = "text/css"
        elif filename_lower.endswith(".py"):
            mime_type = "text/x-python"
        elif filename_lower.endswith(".js"):
            mime_type = "text/javascript"
        else:
            mime_type = mime_type or "text/plain"

        return [{
            "type": "file",
            "url": None,
            "data": encoded,
            "filename": filename,
            "mime_type": mime_type,
        }]

    def infer_zip_bundle_from_prompt_and_response(self, prompt: str, output_text: str) -> dict | None:
        combined = f"{prompt}\n{output_text}"
        mentions_zip = ("zip" in combined.lower()) or ("压缩" in combined)
        mentions_multi = any(token in combined for token in ["两个", "2个", "两份", "两 个", "two", "多个"])
        mentions_txt = ".txt" in combined.lower() or "txt文件" in combined or "txt file" in combined.lower()

        if not (mentions_zip and mentions_multi and mentions_txt):
            return None

        content = self.extract_content_from_prompt(prompt)
        if content is None:
            content = self.extract_content_from_response(output_text)
        if content is None:
            return None

        zip_name = self.extract_zip_filename(combined) or "bundle.zip"
        file_names = self.extract_multiple_txt_filenames(combined)
        if len(file_names) < 2:
            file_names = ["file1.txt", "file2.txt"]

        zip_bytes = self.build_zip_bytes({
            file_names[0]: content,
            file_names[1]: content,
        })

        return {
            "type": "archive",
            "url": None,
            "data": base64.b64encode(zip_bytes).decode("ascii"),
            "filename": zip_name,
            "mime_type": "application/zip",
        }

    def build_zip_bytes(self, files: dict[str, str]) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for name, content in files.items():
                zf.writestr(self.sanitize_filename(name), content)
        return buffer.getvalue()

    def extract_multiple_txt_filenames(self, text: str) -> list[str]:
        names = re.findall(r'([A-Za-z0-9_.-]+\.txt)', text, flags=re.IGNORECASE)
        cleaned = []
        seen = set()
        for name in names:
            safe = self.sanitize_filename(name)
            if safe.lower().endswith(".txt") and safe not in seen:
                seen.add(safe)
                cleaned.append(safe)
        return cleaned

    def extract_zip_filename(self, text: str) -> str | None:
        match = re.search(r'([A-Za-z0-9_.-]+\.zip)', text, flags=re.IGNORECASE)
        if match:
            return self.sanitize_filename(match.group(1))
        return None

    def extract_filename(self, text: str) -> str | None:
        if not text:
            return None
        match = re.search(r'([A-Za-z0-9_.-]+\.(?:txt|md|csv|json|py|js|html|css|zip))', text, flags=re.IGNORECASE)
        if match:
            return self.sanitize_filename(match.group(1))
        return None

    def extract_content_from_prompt(self, prompt: str) -> str | None:
        patterns = [
            r'内容是[：: ]*["\'`]?(.+?)["\'`]?(?:[,，。；;]|然后|并发给我|并给我|并发送给我|并压缩|并打包|$)',
            r'内容为[：: ]*["\'`]?(.+?)["\'`]?(?:[,，。；;]|然后|并发给我|并给我|并发送给我|并压缩|并打包|$)',
            r'内容写[：: ]*["\'`]?(.+?)["\'`]?(?:[,，。；;]|然后|并发给我|并给我|并发送给我|并压缩|并打包|$)',
            r'content is\s*["\']?(.+?)["\']?(?:[,.;]| and | then |$)',
            r'with content\s*["\']?(.+?)["\']?(?:[,.;]| and | then |$)',
        ]
        for pattern in patterns:
            match = re.search(pattern, prompt, flags=re.IGNORECASE | re.DOTALL)
            if match:
                value = match.group(1).strip()
                if value:
                    return self.clean_inferred_content(value)
        return None

    def extract_content_from_response(self, text: str) -> str | None:
        patterns = [
            r'内容为["\']?(.+?)["\']?(?:[,，。；;]|$)',
            r'内容是["\']?(.+?)["\']?(?:[,，。；;]|$)',
            r'包含["\']?(.+?)["\']?(?:[,，。；;]|$)',
            r'content(?: is|:)\s*["\']?(.+?)["\']?(?:[,.;]|$)',
        ]
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
            if match:
                value = match.group(1).strip()
                if value:
                    return self.clean_inferred_content(value)
        return None

    def clean_inferred_content(self, value: str) -> str:
        value = value.strip()
        value = value.strip("\"'` ")
        value = re.sub(r"\s+$", "", value)
        return value

    def persist_candidate(self, task, candidate: dict, index: int):
        user_dir = self.user_artifact_dir(task.user_id)
        user_dir.mkdir(parents=True, exist_ok=True)

        filename = self.choose_filename(candidate, index=index)
        kind = self.detect_kind(candidate, filename)
        mime_type = self.choose_mime_type(candidate, filename)

        binary = None
        source_type = "inline"
        source_url = candidate.get("url")

        if candidate.get("data"):
            binary = self.decode_data(candidate["data"])
            source_type = "inline"
        elif source_url and not source_url.startswith(SKIP_URL_PREFIXES):
            binary = self.fetch_remote_bytes(source_url)
            source_type = "remote_url"
        else:
            return None

        if not binary:
            return None

        storage_name = f"{uuid.uuid4().hex}_{self.sanitize_filename(filename)}"
        relative_path = f"user_{task.user_id}/{storage_name}"
        file_path = user_dir / storage_name
        file_path.write_bytes(binary)

        sha256 = hashlib.sha256(binary).hexdigest()
        size_bytes = len(binary)

        artifact = GeneratedArtifact(
            task_id=task.id,
            user_id=task.user_id,
            kind=kind,
            source_type=source_type,
            filename=filename,
            storage_name=storage_name,
            relative_path=relative_path,
            mime_type=mime_type,
            size_bytes=size_bytes,
            sha256=sha256,
            source_url=source_url,
        )
        return artifact

    def user_artifact_dir(self, user_id: int) -> Path:
        root = Path(current_app.root_path).parent / current_app.config["OPENCLAW_ARTIFACTS_DIR"]
        return root / f"user_{user_id}"

    def artifact_abspath(self, artifact: GeneratedArtifact) -> Path:
        root = Path(current_app.root_path).parent / current_app.config["OPENCLAW_ARTIFACTS_DIR"]
        return root / artifact.relative_path

    def extract_artifact_candidates(self, data: dict) -> list[dict]:
        found = []

        def walk(node):
            if isinstance(node, dict):
                candidate = self.build_candidate(node)
                if candidate:
                    found.append(candidate)
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        walk(data)

        unique = []
        seen = set()
        for item in found:
            signature = json.dumps(item, sort_keys=True, ensure_ascii=False, default=str)
            if signature not in seen:
                seen.add(signature)
                unique.append(item)
        return unique

    def build_candidate(self, node: dict):
        node_type = str(node.get("type") or "").strip().lower()

        url = None
        for key in ARTIFACT_URL_KEYS:
            value = node.get(key)
            if isinstance(value, str) and value.strip():
                url = value.strip()
                break

        data = None
        for key in ARTIFACT_DATA_KEYS:
            value = node.get(key)
            if isinstance(value, str) and value.strip():
                data = value.strip()
                break

        if not url and not data:
            return None

        if url and url.startswith(SKIP_URL_PREFIXES) and not data:
            return None

        filename = None
        for key in ARTIFACT_FILENAME_KEYS:
            value = node.get(key)
            if isinstance(value, str) and value.strip():
                filename = value.strip()
                break

        mime_type = None
        for key in ARTIFACT_MIME_KEYS:
            value = node.get(key)
            if isinstance(value, str) and value.strip():
                mime_type = value.strip()
                break

        return {
            "type": node_type or "file",
            "url": url,
            "data": data,
            "filename": filename,
            "mime_type": mime_type,
        }

    def choose_filename(self, candidate: dict, index: int) -> str:
        filename = candidate.get("filename")
        if filename:
            return self.sanitize_filename(filename)

        url = candidate.get("url")
        if url:
            path = urlparse(url).path
            tail = Path(path).name.strip()
            if tail:
                return self.sanitize_filename(tail)

        ext = mimetypes.guess_extension(candidate.get("mime_type") or "") or ""
        base = candidate.get("type") or "artifact"
        return self.sanitize_filename(f"{base}_{index}{ext}")

    def choose_mime_type(self, candidate: dict, filename: str) -> str:
        mime_type = candidate.get("mime_type")
        if mime_type:
            return mime_type
        guessed, _ = mimetypes.guess_type(filename)
        return guessed or "application/octet-stream"

    def detect_kind(self, candidate: dict, filename: str) -> str:
        node_type = (candidate.get("type") or "").lower()
        if node_type in ARTIFACT_KIND_TYPES:
            return ARTIFACT_KIND_TYPES[node_type]

        mime_type = (candidate.get("mime_type") or "").lower()
        if mime_type.startswith("image/"):
            return "image"
        if "zip" in mime_type or filename.lower().endswith(".zip"):
            return "archive"
        return "file"

    def decode_data(self, data: str) -> bytes:
        raw = data.strip()
        if raw.startswith("data:") and "," in raw:
            raw = raw.split(",", 1)[1]
        raw = re.sub(r"\s+", "", raw)
        return base64.b64decode(raw, validate=False)

    def fetch_remote_bytes(self, url: str) -> bytes:
        timeout = (
            int(current_app.config["OPENCLAW_CONNECT_TIMEOUT"]),
            int(current_app.config["OPENCLAW_READ_TIMEOUT"]),
        )
        headers = {}
        if self.should_forward_gateway_auth(url):
            token = current_app.config.get("OPENCLAW_GATEWAY_TOKEN")
            if token:
                headers["Authorization"] = f"Bearer {token}"

        response = requests.get(url, headers=headers, timeout=timeout)
        response.raise_for_status()
        return response.content

    def should_forward_gateway_auth(self, url: str) -> bool:
        try:
            base = current_app.config["OPENCLAW_BASE_URL"].rstrip("/")
            return url.startswith(base)
        except Exception:
            return False

    def sanitize_filename(self, value: str) -> str:
        value = Path(value).name
        value = re.sub(r"[^a-zA-Z0-9._-]+", "_", value).strip("._")
        return value[:180] or "artifact"
