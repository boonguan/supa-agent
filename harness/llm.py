import json
import os
import time
import urllib.error
import urllib.request

RETRYABLE_CODES = (429, 500, 502, 503, 504)

DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-v4-flash"
SUPPORTED_MODELS = ["deepseek-v4-pro", "deepseek-v4-flash"]

# 各模型家族支持的推理强度 (前缀匹配, 先长后短); 不在表里的模型不发 reasoning_effort 参数。
# DeepSeek v4 档位为 low/high/max (无 medium), none 表示关闭思考 (thinking: disabled),
# 见 https://api-docs.deepseek.com/zh-cn/guides/thinking_mode
EFFORT_RULES = (
    ("deepseek", ("none", "low", "high", "max")),
    ("gpt-5", ("low", "medium", "high")),
    ("o3", ("low", "medium", "high")),
    ("o4", ("low", "medium", "high")),
)

_ORDER = ("none", "low", "medium", "high", "max")


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
        self.effort = effort or os.environ.get("LLM_EFFORT", "high")  # DeepSeek 思考模式默认 high
        self._resp = None
        self._aborted = False
        if not self.api_key:
            raise LLMError("LLM_API_KEY 未设置")

    def abort(self):
        """中断当前请求: 直接关闭连接, 让阻塞中的读立即返回。"""
        self._aborted = True
        resp, self._resp = self._resp, None
        if resp is not None:
            try:
                resp.close()
            except OSError:
                pass

    def _post(self, payload, retries=3):
        data = json.dumps(payload).encode()
        for attempt in range(retries):
            req = urllib.request.Request(
                f"{self.base_url}/chat/completions",
                data=data,
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
                if e.code in RETRYABLE_CODES and attempt < retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                raise LLMError(f"API 返回 {e.code}: {detail[:500]}") from e
            except urllib.error.URLError as e:
                if attempt < retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                raise LLMError(f"网络错误: {e.reason}") from e

    def effective_effort(self):
        """当前模型实际生效的推理强度: 不支持返回 None, 不在档位表时就近向上取 (如 medium -> high)。"""
        efforts = supported_efforts(self.model)
        if not efforts:
            return None
        if self.effort in efforts:
            return self.effort
        try:
            want = _ORDER.index(self.effort)
        except ValueError:
            return efforts[-1]
        for level in _ORDER[want:]:
            if level in efforts:
                return level
        return efforts[-1]

    def _payload(self, messages, tools=None, stream=False):
        payload = {"model": self.model, "messages": messages}
        if tools:
            payload["tools"] = tools
        if stream:
            payload["stream"] = True
            payload["stream_options"] = {"include_usage": True}  # 末尾 chunk 带 usage (token 统计)
        effort = self.effective_effort()
        if effort == "none":
            payload["thinking"] = {"type": "disabled"}
        elif effort:
            payload["reasoning_effort"] = effort
        return payload

    def chat(self, messages, tools=None):
        with self._post(self._payload(messages, tools)) as resp:
            return json.loads(resp.read().decode())

    def chat_stream(self, messages, tools=None):
        self._aborted = False
        with self._post(self._payload(messages, tools, stream=True)) as resp:
            self._resp = resp
            try:
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
            except (OSError, ValueError) as e:
                if self._aborted:
                    raise KeyboardInterrupt from None
                raise LLMError(f"流式连接中断: {e}") from e
            finally:
                self._resp = None
