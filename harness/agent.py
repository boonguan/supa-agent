import json

from .context import build_system_prompt
from .tools import TOOLS
from .ui import C, MdStream, result_preview, tool_line


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


def _arg_summary(name, args):
    """每种工具挑最关键的参数做单行摘要。"""
    if name == "bash":
        return args.get("command", "")
    if name in ("read_file", "write_file", "edit_file"):
        return args.get("path", "")
    if name == "list_dir":
        return args.get("path", ".")
    if name == "grep":
        return f"{args.get('pattern', '')}  {args.get('path', '.')}"
    if name == "task":
        return args.get("description", "")
    if name == "remember":
        return args.get("fact", "")
    if name == "todo_write":
        return f"{len(args.get('todos', []))} 项"
    return json.dumps(args, ensure_ascii=False)[:120]


class Agent:
    def __init__(self, llm, cwd, max_steps=30, depth=0):
        self.llm = llm
        self.cwd = cwd
        self.max_steps = max_steps
        self.depth = depth
        self.todos = []
        self.show_reasoning = False  # 思维链默认折叠, /reasoning 切换
        self.messages = [{"role": "system", "content": build_system_prompt(llm.model, cwd)}]

    def set_model(self, name):
        self.llm.model = name
        self.refresh_system()

    def refresh_system(self):
        """模型 / cwd / 记忆变化后重建系统提示。"""
        self.messages[0] = {"role": "system", "content": build_system_prompt(self.llm.model, self.cwd)}

    def reset(self):
        self.refresh_system()
        self.messages = [self.messages[0]]
        self.todos = []

    @property
    def tools(self):
        if self.depth >= 1:
            return [t for t in TOOLS if t.name != "task"]  # 子代理不能再派生
        return TOOLS

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
            for t in self.tools
        ]

    def _end_reasoning(self, reasoning):
        if self.show_reasoning:
            print()
        else:  # 收起进度行
            print(f"\r{C.DIM}✱ 已思考 ({sum(len(r) for r in reasoning)} 字){' ' * 12}{C.RESET}")

    def _stream(self):
        content, reasoning = [], []
        accumulator = _ToolAccumulator()
        md = MdStream() if self.depth == 0 else None  # 正文按行渲染 markdown; 子代理不刷屏
        for chunk in self.llm.chat_stream(self.messages, tools=self.schemas):
            delta = chunk["choices"][0].get("delta", {})
            if delta.get("reasoning_content"):
                reasoning.append(delta["reasoning_content"])
                if md:
                    if self.show_reasoning:
                        print(f"{C.DIM}{delta['reasoning_content']}{C.RESET}", end="", flush=True)
                    else:
                        print(f"\r{C.DIM}✱ 思考中… {sum(len(r) for r in reasoning)} 字{C.RESET}", end="", flush=True)
            if delta.get("content"):
                if md and reasoning and not content:
                    self._end_reasoning(reasoning)
                content.append(delta["content"])
                if md:
                    md.feed(delta["content"])
            accumulator.add(delta)
        if md:
            if reasoning and not content:
                self._end_reasoning(reasoning)
            md.close()
        return "".join(content), "".join(reasoning), accumulator.to_calls()

    def _assistant_message(self, content, reasoning, tool_calls=None):
        msg = {"role": "assistant", "content": content or None}
        if reasoning:
            # DeepSeek 思考模式: 带 tools 的请求必须回传 reasoning_content, 否则 400
            msg["reasoning_content"] = reasoning
        if tool_calls:
            msg["tool_calls"] = tool_calls
        return msg

    def chat(self, task):
        self.messages.append({"role": "user", "content": task})
        for _ in range(self.max_steps):
            content, reasoning, tool_calls = self._stream()
            if not tool_calls:
                self.messages.append(self._assistant_message(content, reasoning))
                return content
            self.messages.append(self._assistant_message(content, reasoning, tool_calls))
            for call in tool_calls:
                name = call["function"]["name"]
                try:
                    args = json.loads(call["function"]["arguments"] or "{}")
                except json.JSONDecodeError:
                    args = {}
                fn = next((t for t in self.tools if t.name == name), None)
                tool_line(name, _arg_summary(name, args), depth=self.depth)
                if fn is None:
                    result = f"未知工具: {name}"
                else:
                    try:
                        result = fn(self, **args)
                    except Exception as e:
                        result = f"工具出错: {type(e).__name__}: {e}"
                if name not in ("todo_write", "edit_file"):  # 这两个工具自带渲染
                    result_preview(result, depth=self.depth)
                self.messages.append(
                    {"role": "tool", "tool_call_id": call["id"], "content": result}
                )
        print(f"{C.DIM}已达到最大步数, 任务未完成{C.RESET}")
        return ""

    def run(self, task):
        self.reset()
        return self.chat(task)
