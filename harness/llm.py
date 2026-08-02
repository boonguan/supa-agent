import json
import os
import urllib.error
import urllib.request

DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-v4-flash"
SUPPORTED_MODELS = ["deepseek-v4-pro", "deepseek-v4-flash"]

# 各模型家族支持的推理强度 (前缀匹配, 先长后短); 不在表里的模型不发 reasoning_effort 参数
EFFORT_RULES = (
    ("deepseek-max", ("low", "medium", "high", "max")),
    ("deepseek", ("low", "medium", "high")),
    ("gpt-5", ("low", "medium", "high")),
    ("o3", ("low", "medium", "high")),
    ("o4", ("low", "medium", "high")),
)


def supported_efforts(model):
    for prefix, efforts in EFFORT_RULES:
        if model.startswith(prefix):
            return efforts
    return ()


class LLMError(Exception):
    pass


class LLM:
    def __init__(self, base_url=None, api_key=None, model=None, effort=None):
        self.base_url = (base_url or os.environ.get("LLM_BASE_URL", DEFAULT_BASE_URL)).rstrip("/")
        self.api_key = api_key or os.environ.get("LLM_API_KEY", "")
        self.model = model or os.environ.get("LLM_MODEL", DEFAULT_MODEL)
        self.effort = effort or os.environ.get("LLM_EFFORT", "medium")
        if not self.api_key:
            raise LLMError("LLM_API_KEY 未设置")

    def _post(self, payload):
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
            return urllib.request.urlopen(req, timeout=600)
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")
            raise LLMError(f"API 返回 {e.code}: {detail[:500]}") from e

    def effective_effort(self):
        """当前模型实际生效的推理强度: 不支持返回 None, 超出上限降到最高档。"""
        efforts = supported_efforts(self.model)
        if not efforts:
            return None
        return self.effort if self.effort in efforts else efforts[-1]

    def _payload(self, messages, tools=None, stream=False):
        payload = {"model": self.model, "messages": messages}
        if tools:
            payload["tools"] = tools
        if stream:
            payload["stream"] = True
        effort = self.effective_effort()
        if effort:
            payload["reasoning_effort"] = effort
        return payload

    def chat(self, messages, tools=None):
        with self._post(self._payload(messages, tools)) as resp:
            return json.loads(resp.read().decode())

    def chat_stream(self, messages, tools=None):
        with self._post(self._payload(messages, tools, stream=True)) as resp:
            for raw in resp:
                line = raw.decode(errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    yield json.loads(data)
                except json.JSONDecodeError:
                    continue
