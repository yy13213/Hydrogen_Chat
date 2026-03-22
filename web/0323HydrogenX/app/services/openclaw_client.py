import json
import re
import time
from typing import Any

import requests
from flask import current_app


class OpenClawGatewayError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None, response_text: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.response_text = response_text


class OpenClawClient:
    RETRYABLE_STATUS_CODES = {429, 502, 503, 504}
    ARTIFACT_TAG_START = "<hydrogenx-artifacts>"
    ARTIFACT_TAG_END = "</hydrogenx-artifacts>"

    def call(
        self,
        prompt: str,
        user_id: int,
        agent_id: str | None = None,
        session_key: str | None = None,
        request_user: str | None = None,
    ) -> dict[str, Any]:
        base_url = current_app.config["OPENCLAW_BASE_URL"]
        token = current_app.config["OPENCLAW_GATEWAY_TOKEN"]
        model = current_app.config["OPENCLAW_MODEL"]
        default_agent = current_app.config["OPENCLAW_DEFAULT_AGENT"]
        retry_times = max(0, int(current_app.config["OPENCLAW_RETRY_TIMES"]))
        retry_backoff = float(current_app.config["OPENCLAW_RETRY_BACKOFF"])
        connect_timeout = int(current_app.config["OPENCLAW_CONNECT_TIMEOUT"])
        read_timeout = int(current_app.config["OPENCLAW_READ_TIMEOUT"])

        if not token:
            raise RuntimeError("OPENCLAW_GATEWAY_TOKEN 未配置。")

        session_key = session_key or f"web:user:{user_id}"
        request_user = request_user or self.build_request_user(session_key, user_id)
        url = f"{base_url}/v1/responses"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "x-openclaw-agent-id": agent_id or default_agent,
            "x-openclaw-session-key": session_key,
        }
        payload = {
            "model": model,
            "input": self.build_protocol_prompt(prompt),
            "stream": False,
            "user": request_user,
        }

        last_error: Exception | None = None
        for attempt in range(retry_times + 1):
            try:
                response = requests.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=(connect_timeout, read_timeout),
                )
                self.raise_for_status(response)

                data = response.json()
                status = data.get("status")
                if status in {"failed", "error", "cancelled"}:
                    raise OpenClawGatewayError(f"OpenClaw 返回异常状态: {status}")

                text = self.extract_output_text(data)
                return {
                    "output_text": text,
                    "raw": data,
                    "artifact_manifest": self.extract_artifact_manifest(text),
                }
            except (requests.Timeout, requests.ConnectionError) as exc:
                last_error = OpenClawGatewayError(f"连接 OpenClaw 网关失败：{exc}")
            except OpenClawGatewayError as exc:
                last_error = exc
                if exc.status_code not in self.RETRYABLE_STATUS_CODES:
                    break
            except requests.RequestException as exc:
                last_error = OpenClawGatewayError(f"OpenClaw 请求失败：{exc}")
                break

            if attempt < retry_times:
                sleep_seconds = retry_backoff * (attempt + 1)
                current_app.logger.warning(
                    "OpenClaw request retrying: attempt=%s/%s sleep=%ss user=%s agent=%s",
                    attempt + 1,
                    retry_times,
                    sleep_seconds,
                    request_user,
                    headers["x-openclaw-agent-id"],
                )
                time.sleep(sleep_seconds)

        raise last_error or OpenClawGatewayError("未知 OpenClaw 调用失败")

    def build_protocol_prompt(self, prompt: str) -> str:
        protocol = '''
你正在通过 HydrogenX 与用户交互。

当你生成文件、图片、压缩包、代码文件、文档、CSV、Markdown、PDF、Word、PPT、Excel、JSON、TXT 等任何可下载产物时：
1. 先正常用自然语言简短说明结果。
2. 然后在回复末尾严格附加一个 XML 标签块，不要放进代码块里：
<hydrogenx-artifacts>JSON</hydrogenx-artifacts>

JSON 格式必须是一个对象：
{
  "artifacts": [
    {
      "kind": "file",
      "filename": "hello.txt",
      "mime_type": "text/plain",
      "content_base64": "aGVsbG8="
    }
  ]
}

要求：
- 若生成的是图片，kind 用 image。
- 若生成的是 zip，kind 用 archive。
- 文本小文件请直接返回 content_base64。
- 如果无法上传附件，也必须改为返回 content_base64，而不是只说“创建成功但无法发送”。
- 不要省略 filename 和 mime_type。
- 如果没有生成任何文件，就不要输出 hydrogenx-artifacts 标签。
'''
        return prompt.rstrip() + "\n\n" + protocol.strip()

    @staticmethod
    def build_request_user(session_key: str, fallback_user_id: int) -> str:
        prefix = "web:user:"
        if session_key.startswith(prefix):
            suffix = session_key[len(prefix):].strip()
        else:
            suffix = str(fallback_user_id)
        suffix = suffix or str(fallback_user_id)
        return f"web-user-{suffix}"

    @staticmethod
    def raise_for_status(response: requests.Response) -> None:
        if response.ok:
            return

        body_preview = OpenClawClient.safe_response_preview(response)
        message = f"OpenClaw 网关请求失败（HTTP {response.status_code}）"
        if response.status_code == 401:
            message += "：gateway token 无效或未被接受。"
        elif response.status_code == 403:
            message += "：当前请求被网关拒绝。"
        elif response.status_code == 404:
            message += "：接口路径可能不对，请确认 /v1/responses 是否可用。"
        elif response.status_code == 502:
            message += "：网关已收到请求，但其上游模型服务返回 Bad Gateway。"
        elif response.status_code == 503:
            message += "：网关或上游模型服务暂时不可用。"
        elif response.status_code == 504:
            message += "：网关等待上游模型响应超时。"

        if body_preview:
            message += f" 响应片段：{body_preview}"

        raise OpenClawGatewayError(
            message=message,
            status_code=response.status_code,
            response_text=body_preview,
        )

    @staticmethod
    def safe_response_preview(response: requests.Response, limit: int = 500) -> str:
        try:
            data = response.json()
            text = json.dumps(data, ensure_ascii=False)
        except ValueError:
            text = (response.text or "").strip()

        text = " ".join(text.split())
        if len(text) > limit:
            return text[:limit] + "..."
        return text

    @classmethod
    def extract_artifact_manifest(cls, text: str) -> dict:
        if not isinstance(text, str) or not text.strip():
            return {}

        pattern = re.escape(cls.ARTIFACT_TAG_START) + r"(.*?)" + re.escape(cls.ARTIFACT_TAG_END)
        match = re.search(pattern, text, flags=re.DOTALL | re.IGNORECASE)
        if not match:
            return {}

        payload = match.group(1).strip()
        if not payload:
            return {}

        try:
            return json.loads(payload)
        except Exception:
            current_app.logger.warning("Failed to parse hydrogenx artifact manifest: %s", payload[:500])
            return {}

    @classmethod
    def strip_artifact_manifest(cls, text: str) -> str:
        if not isinstance(text, str):
            return ""
        pattern = re.escape(cls.ARTIFACT_TAG_START) + r".*?" + re.escape(cls.ARTIFACT_TAG_END)
        stripped = re.sub(pattern, "", text, flags=re.DOTALL | re.IGNORECASE)
        return stripped.strip()

    @classmethod
    def extract_output_text(cls, data: dict) -> str:
        direct_text = data.get("output_text")
        if isinstance(direct_text, str) and direct_text.strip():
            return cls.strip_artifact_manifest(direct_text.strip())

        parts: list[str] = []
        for item in data.get("output", []):
            if not isinstance(item, dict):
                continue

            if item.get("type") == "message":
                for content in item.get("content", []):
                    if not isinstance(content, dict):
                        continue
                    content_type = content.get("type")
                    text = content.get("text")
                    if content_type in {"output_text", "text"} and isinstance(text, str):
                        parts.append(text)
            else:
                text = item.get("text")
                if item.get("type") in {"output_text", "text"} and isinstance(text, str):
                    parts.append(text)

        if parts:
            return cls.strip_artifact_manifest("\n".join(parts).strip())

        return json.dumps(data, ensure_ascii=False, indent=2)
