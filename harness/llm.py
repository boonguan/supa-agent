import json
import os
import urllib.error
import urllib.request

DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-chat"


class LLMError(Exception):
    pass


class LLM:
    def __init__(self, base_url=None, api_key=None, model=None):
        self.base_url = (base_url or os.environ.get("LLM_BASE_URL", DEFAULT_BASE_URL)).rstrip("/")
        self.api_key = api_key or os.environ.get("LLM_API_KEY", "")
        self.model = model or os.environ.get("LLM_MODEL", DEFAULT_MODEL)
        if not self.api_key:
            raise LLMError("LLM_API_KEY 未设置")

    def chat(self, messages, tools=None):
        payload = {"model": self.model, "messages": messages}
        if tools:
            payload["tools"] = tools
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")
            raise LLMError(f"API 返回 {e.code}: {detail[:500]}") from e
