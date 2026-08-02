import json

from .llm import LLM
from .tools import TOOLS

SYSTEM_PROMPT = """你是一个运行在用户 shell 里的 coding agent。
你可以调用工具来查看、修改文件和执行命令。规则:
1. 先探索再动手, 用 list_dir / read_file 了解现状。
2. 用 bash 运行测试或验证你的修改。
3. 完成后用自然语言总结你做了什么。"""


class _ToolAccumulator:
    def __init__(self):
        self.parts = {}

    def add(self, delta):
        for tc in delta.get("tool_calls", []):
            idx = tc["index"]
            part = self.parts.setdefault(idx, {"id": "", "name": "", "arguments": ""})
            if tc.get("id"):
                part["id"] = tc["id"]
            fn = tc.get("function") or {}
            if fn.get("name"):
                part["name"] += fn["name"]
            if fn.get("arguments"):
                part["arguments"] += fn["arguments"]

    def to_calls(self):
        calls = []
        for idx in sorted(self.parts):
            p = self.parts[idx]
            calls.append(
                {
                    "id": p["id"] or f"call_{idx}",
                    "type": "function",
                    "function": {"name": p["name"], "arguments": p["arguments"]},
                }
            )
        return calls


class Agent:
    def __init__(self, llm, cwd, max_steps=30):
        self.llm = llm
        self.cwd = cwd
        self.max_steps = max_steps
        self.messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    def reset(self):
        self.messages = [self.messages[0]]

    @property
    def schemas(self):
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in TOOLS
        ]

    def _stream(self):
        content = []
        accumulator = _ToolAccumulator()
        for chunk in self.llm.chat_stream(self.messages, tools=self.schemas):
            delta = chunk["choices"][0].get("delta", {})
            if delta.get("content"):
                content.append(delta["content"])
                print(delta["content"], end="", flush=True)
            accumulator.add(delta)
        print()
        return "".join(content), accumulator.to_calls()

    def chat(self, task):
        self.messages.append({"role": "user", "content": task})
        for step in range(1, self.max_steps + 1):
            print(f"\n--- step {step} ---")
            content, tool_calls = self._stream()
            if not tool_calls:
                return content
            self.messages.append(
                {
                    "role": "assistant",
                    "content": content or None,
                    "tool_calls": tool_calls,
                }
            )
            for call in tool_calls:
                fn = next(t for t in TOOLS if t.name == call["function"]["name"])
                try:
                    args = json.loads(call["function"]["arguments"] or "{}")
                except json.JSONDecodeError:
                    args = {}
                print(f"  {call['function']['name']}({json.dumps(args, ensure_ascii=False)[:200]})")
                try:
                    result = fn(self.cwd, **args)
                except Exception as e:
                    result = f"工具出错: {type(e).__name__}: {e}"
                self.messages.append(
                    {"role": "tool", "tool_call_id": call["id"], "content": result}
                )
        print("已达到最大步数, 任务未完成")
        return ""

    def run(self, task):
        self.reset()
        return self.chat(task)
