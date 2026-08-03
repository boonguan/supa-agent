import json

from . import session
from .context import build_system_prompt
from .policy import Policy
from .tools import TOOLS
from .ui import ConsoleUI

COMPACT_THRESHOLD = 0.7  # 上下文占比超过即自动压缩
PRUNE_THRESHOLD = 0.5  # 超过即裁剪旧工具输出
PRUNE_KEEP_RECENT = 16  # 最近 N 条消息不裁剪


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
        self.last_usage = None  # 上一轮请求的 usage
        self.total_usage = {}  # 本会话累计 token
        self.session_id = session.new_id() if depth == 0 else None
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
        self._record_usage(usage)
        return "".join(content), "".join(reasoning), accumulator.to_calls()

    def _record_usage(self, usage):
        if not usage:
            return
        self.last_usage = usage
        t = self.total_usage
        for k in ("prompt_tokens", "completion_tokens"):
            t[k] = t.get(k, 0) + (usage.get(k) or 0)
        rt = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0
        t["reasoning_tokens"] = t.get("reasoning_tokens", 0) + rt
        t["requests"] = t.get("requests", 0) + 1

    def context_used(self):
        """上一轮请求占上下文窗口的比例 (0~1)。"""
        if not self.last_usage:
            return 0.0
        used = (self.last_usage.get("prompt_tokens") or 0) + (self.last_usage.get("completion_tokens") or 0)
        return used / self.llm.context_window()

    PRUNE_MARK = "\n...(旧工具输出已裁剪)"

    def _prune_old_tool_results(self):
        """裁剪早期轮次的大段工具输出, 便宜地释放上下文。"""
        for m in self.messages[:-PRUNE_KEEP_RECENT]:
            content = m.get("content") or ""
            if m["role"] == "tool" and len(content) > 400 and not content.endswith(self.PRUNE_MARK):
                m["content"] = content[:200] + self.PRUNE_MARK

    def compact(self):
        """把较早的对话轮次总结成一条摘要消息, 保留当前轮完整。返回是否执行了压缩。"""
        user_idxs = [
            i for i, m in enumerate(self.messages)
            if m["role"] == "user" and not (m.get("content") or "").startswith("(历史摘要)")
        ]
        if len(user_idxs) < 2:
            return False  # 只有一轮, 无可压缩
        cut = user_idxs[-1]
        lines = []
        for m in self.messages[1:cut]:
            text = m.get("content") or ""
            if m.get("tool_calls"):
                text += " | 调用: " + ", ".join(c["function"]["name"] for c in m["tool_calls"])
            lines.append(f"[{m['role']}] {text[:2000]}")
        transcript = "\n".join(lines)[-30000:]
        resp = self.llm.chat([
            {
                "role": "user",
                "content": "把下面的对话历史压缩成简洁摘要, 必须保留: 用户目标与约束、已修改的文件清单、"
                           "关键决定、未完成事项。直接输出摘要:\n\n" + transcript,
            }
        ])
        summary = resp["choices"][0]["message"]["content"]
        self.messages[1:cut] = [
            {"role": "user", "content": "(历史摘要) 之前对话的压缩记录:\n" + summary},
            {"role": "assistant", "content": "已了解之前的进展, 继续。"},
        ]
        self.last_usage = None  # 旧计数已失效
        return True

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
        finally:
            if self.depth == 0:
                session.save(self)

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
            if self.context_used() > PRUNE_THRESHOLD:
                self._prune_old_tool_results()
            if self.context_used() > COMPACT_THRESHOLD:
                self.ui.notice("上下文接近上限, 自动压缩历史…")
                try:
                    if self.compact():
                        self.ui.notice("历史已压缩")
                except Exception as e:
                    self.ui.notice(f"压缩失败: {type(e).__name__}: {e}")
        self.ui.notice("已达到最大步数, 任务未完成")
        return ""

    def run(self, task):
        self.reset()
        return self.chat(task)
