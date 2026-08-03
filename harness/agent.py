import json

from .context import build_system_prompt
from .policy import Policy
from .tools import TOOLS
from .ui import ConsoleUI


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


def _est_tokens(text):
    """粗估 token 数: 中日韩字符约 0.6 token/字, 其余约 4 字符/token。"""
    import unicodedata

    cjk = sum(1 for c in text if unicodedata.east_asian_width(c) in "WF")
    return max(1, int(cjk * 0.6 + (len(text) - cjk) / 4))


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
    def __init__(self, llm, cwd, max_steps=30, depth=0, ui=None, policy=None):
        self.llm = llm
        self.cwd = cwd
        self.max_steps = max_steps
        self.depth = depth
        self.ui = ui or ConsoleUI()
        self.policy = policy or Policy()
        self.todos = []
        self.tool_log = []  # (name, summary, result), /output 回看
        self.abort = False  # TUI ctrl-c 置位, 流式循环检测后中断
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

    @staticmethod
    def _reason_label(reasoning, usage):
        # usage 里有真实 reasoning_tokens 就用, 没有 (正文已开始/端点不支持) 用估算
        tokens = (usage or {}).get("completion_tokens_details", {}).get("reasoning_tokens")
        return f"{tokens} tokens" if tokens else f"~{_est_tokens(''.join(reasoning))} tokens"

    def _stream(self):
        content, reasoning = [], []
        usage = None
        accumulator = _ToolAccumulator()
        for chunk in self.llm.chat_stream(self.messages, tools=self.schemas):
            if self.abort:
                raise KeyboardInterrupt
            if chunk.get("usage"):
                usage = chunk["usage"]
            choices = chunk.get("choices") or []
            if not choices:  # include_usage 的末尾 chunk 没有 choices
                continue
            delta = choices[0].get("delta", {})
            if delta.get("reasoning_content"):
                reasoning.append(delta["reasoning_content"])
                self.ui.on_reasoning(delta["reasoning_content"], _est_tokens("".join(reasoning)), self.depth)
            if delta.get("content"):
                if reasoning and not content:
                    self.ui.end_reasoning(self._reason_label(reasoning, None), "".join(reasoning), self.depth)
                content.append(delta["content"])
                self.ui.on_content(delta["content"], self.depth)
            accumulator.add(delta)
        if reasoning and not content:
            self.ui.end_reasoning(self._reason_label(reasoning, usage), "".join(reasoning), self.depth)
        self.ui.end_content(self.depth)
        return "".join(content), "".join(reasoning), accumulator.to_calls()

    def _assistant_message(self, content, reasoning, tool_calls=None):
        msg = {"role": "assistant", "content": content or None}
        if reasoning:
            # DeepSeek 思考模式: 带 tools 的请求必须回传 reasoning_content, 否则 400
            msg["reasoning_content"] = reasoning
        if tool_calls:
            msg["tool_calls"] = tool_calls
        return msg

    def _interrupt_cleanup(self):
        """中断后补齐缺失的 tool 回复: assistant(tool_calls) 后每个 call 必须有
        对应 tool 消息, 否则下一轮请求会被 API 拒绝 (400)。"""
        for i in range(len(self.messages) - 1, -1, -1):
            m = self.messages[i]
            if m["role"] == "user":
                return
            if m["role"] == "assistant":
                answered = {t.get("tool_call_id") for t in self.messages[i + 1:] if t["role"] == "tool"}
                for call in m.get("tool_calls") or []:
                    if call["id"] not in answered:
                        self.messages.append(
                            {"role": "tool", "tool_call_id": call["id"], "content": "(用户中断, 未执行)"}
                        )
                return

    def chat(self, task):
        try:
            return self._chat(task)
        except KeyboardInterrupt:
            self._interrupt_cleanup()
            raise

    def _chat(self, task):
        self.abort = False
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
                if self.abort:
                    raise KeyboardInterrupt
                fn = next((t for t in self.tools if t.name == name), None)
                summary = _arg_summary(name, args)
                self.ui.tool_call(name, summary, self.depth)
                if fn is None:
                    result = f"未知工具: {name}"
                else:
                    allowed = True
                    if self.policy.check(name, args, getattr(fn, "readonly", False)) == "ask":
                        answer = self.ui.confirm(name, summary, self.depth)
                        if answer == "a":
                            self.policy.remember(name, args)
                        elif answer != "y":
                            allowed = False
                    if not allowed:
                        result = "用户拒绝执行该操作, 请改用其他方式或询问用户"
                    else:
                        try:
                            result = fn(self, **args)
                        except Exception as e:
                            result = f"工具出错: {type(e).__name__}: {e}"
                self.tool_log.append((name, summary, result))
                del self.tool_log[:-50]
                self.ui.tool_result(name, result, self.depth)
                self.messages.append(
                    {"role": "tool", "tool_call_id": call["id"], "content": result}
                )
        self.ui.notice("已达到最大步数, 任务未完成")
        return ""

    def run(self, task):
        self.reset()
        return self.chat(task)
