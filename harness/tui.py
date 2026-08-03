import contextlib
import io
import threading

from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import ANSI, to_formatted_text
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Float, FloatContainer, HSplit, Layout, Window
from prompt_toolkit.layout.margins import ScrollbarMargin
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.menus import CompletionsMenu
from prompt_toolkit.layout.processors import AfterInput, BeforeInput, ConditionalProcessor
from prompt_toolkit.mouse_events import MouseEventType
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import Frame
from prompt_toolkit.widgets import base as _widgets_base

from .llm import LLMError, SUPPORTED_MODELS, supported_efforts
from .ui import C, _truncate_width, print_diff, print_todos

# Frame 默认直角边框, 改成 opencode 的圆角
_widgets_base.Border.TOP_LEFT = "╭"
_widgets_base.Border.TOP_RIGHT = "╮"
_widgets_base.Border.BOTTOM_LEFT = "╰"
_widgets_base.Border.BOTTOM_RIGHT = "╯"

COMMANDS = ["/exit", "/reset", "/model", "/effort", "/cwd", "/skills", "/memory", "/todos",
            "/reasoning", "/verbose", "/output", "/compact", "/sessions", "/resume",
            "/jobs", "/cost", "/auto", "/help"]

PLACEHOLDER = "输入任务, / 查看命令 · Enter 发送 · Alt+Enter 换行"

STYLE = Style.from_dict(
    {
        "frame.border": "#4b5563",
        "arrow": "bold #34d399",
        "placeholder": "italic #6b7280",
        "completion-menu.completion": "bg:#1f2937 #d1d5db",
        "completion-menu.completion.current": "bg:#0ea5e9 #ffffff",
        "status": "#6b7280",
        "status.model": "#34d399",
        "status.effort": "#fbbf24",
        "status.cwd": "#93c5fd",
        "tool.bullet": "#34d399",
        "tool.name": "bold",
        "dim": "#6b7280",
        "user": "bg:#374151 bold #34d399",
        "user.text": "bg:#374151 #e5e7eb",
        "approval": "bold #fbbf24",
        "approval.option": "#d1d5db",
        "approval.selected": "bg:#374151 bold #fbbf24",
    }
)


class SlashCompleter(Completer):
    def __init__(self, agent):
        self.agent = agent

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if text.startswith("/model "):
            prefix = text[len("/model "):]
            for model in SUPPORTED_MODELS:
                if model.startswith(prefix):
                    yield Completion(model, start_position=-len(prefix))
            return
        if text.startswith("/effort "):
            prefix = text[len("/effort "):]
            for effort in supported_efforts(self.agent.llm.model):
                if effort.startswith(prefix):
                    yield Completion(effort, start_position=-len(prefix))
            return
        if text.startswith("/"):
            for cmd in COMMANDS:
                if cmd.startswith(text):
                    yield Completion(cmd, start_position=-len(text))


def _capture(fn, *args):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        fn(*args)
    return buf.getvalue().rstrip("\n")


class TranscriptUI:
    """结构化会话区: 工具/思考块可点击展开折叠 (CC 风格)。实现 ConsoleUI 同款回调接口。"""

    def __init__(self):
        self.verbose = False  # 新工具块默认展开与否
        self.show_reasoning = False  # 新思考块默认展开与否
        self.blocks = []
        self.pending = None  # 等待 y/n/a 确认的 approval 块
        self._approval_event = threading.Event()
        self._text_block = None
        self._md = None
        self._reasoning = None
        self._pending_tools = []  # FIFO: 并行执行时 tool_result 按序回填
        self._ansi_cache = {}  # id(text_block) -> (len, fragments)
        self.follow = True  # 跟随底部: 光标锚点放末行, Window 自动滚动保持可见
        self.anchor = 0  # 不跟随时的锚点行号
        self.total_lines = 0  # 上次渲染的总行数
        self.on_change = lambda: None  # 新内容产生: 重绘并跟随底部
        self.on_redraw = lambda: None  # 仅重绘 (展开/折叠等), 不动滚动位置

    # --- 写入 ---

    def _text(self, line):
        if self._text_block is None:
            self._text_block = {"kind": "text", "ansi": ""}
            self.blocks.append(self._text_block)
        self._text_block["ansi"] += line.expandtabs(4) + "\n"  # \t 会被渲染成 ^I
        self.on_change()

    def ansi(self, text):
        """整段 ANSI 文本 (命令输出等), 独立成块"""
        self._text_block = None
        if text:
            self._text(text)
        self._text_block = None

    def user(self, text):
        self._text_block = None
        self.blocks.append({"kind": "user", "text": text})
        self._text_block = None
        self.on_change()

    def on_reasoning(self, delta, est_tokens, depth):
        if depth:
            return
        if self._reasoning is None:
            self._reasoning = {"kind": "reasoning", "text": "", "label": "", "expanded": self.show_reasoning, "done": False}
            self.blocks.append(self._reasoning)
        self._reasoning["text"] += delta
        self._reasoning["label"] = f"✱ 思考中… ~{est_tokens} tokens"
        self.on_change()

    def end_reasoning(self, label, text, depth):
        if depth or self._reasoning is None:
            return
        self._reasoning.update(label=f"✱ 已思考 ({label})", text=text, done=True)
        self._reasoning = None
        self.on_change()

    def on_content(self, delta, depth):
        if depth:
            return
        if self._md is None:
            from .ui import MdStream

            self._md = MdStream(printer=self._text)
        self._md.feed(delta)

    def end_content(self, depth):
        if self._md is not None:
            self._md.close()
            self._md = None
        self._text_block = None

    def tool_call(self, name, summary, depth):
        self._text_block = None
        block = {
            "kind": "tool",
            "name": name,
            "summary": " ".join(summary.split()),
            "result": "",
            "depth": depth,
            "expanded": self.verbose,
        }
        self._pending_tools.append(block)
        self.blocks.append(block)
        self.on_change()

    def tool_result(self, name, result, depth):
        if self._pending_tools:
            self._pending_tools.pop(0)["result"] = str(result)
        self.on_change()

    def diff(self, old, new):
        self.ansi(_capture(print_diff, old, new))

    def todos(self, todos):
        self.ansi(_capture(print_todos, todos))

    def notice(self, text):
        self.ansi(f"{C.DIM}{text}{C.RESET}")

    APPROVAL_OPTIONS = (
        ("y", "允许"),
        ("a", "允许, 本会话不再询问同类操作"),
        ("auto", "开启自动审核 (后续修改类操作由模型审批)"),
        ("n", "拒绝"),
    )

    def confirm(self, name, summary, depth):
        """agent 线程阻塞等待用户选择 (按键/鼠标绑定在 run_app 里)。"""
        block = {"kind": "approval", "name": name, "summary": summary, "answer": None, "selected": 0}
        self.blocks.append(block)
        self._approval_event.clear()
        self.pending = block
        self.on_change()
        self._approval_event.wait()
        self.pending = None
        self.on_change()
        return block["answer"] or "n"

    def answer_pending(self, answer):
        if self.pending is not None:
            self.pending["answer"] = answer
            self._approval_event.set()

    def scroll(self, delta):
        """移动锚点行 (滚轮/PgUp/PgDn 共用): 向上先脱离跟随, 滚到底自动恢复跟随。"""
        if delta < 0:
            if self.follow:
                self.follow = False
                self.anchor = max(0, self.total_lines - 1)
            self.anchor = max(0, self.anchor + delta)
        else:
            self.anchor += delta
            if self.anchor >= self.total_lines - 1:
                self.follow = True
        self.on_redraw()

    # --- 渲染 ---

    def _toggle(self, block):
        def handler(mouse_event):
            if mouse_event.event_type == MouseEventType.MOUSE_UP:
                block["expanded"] = not block["expanded"]
                self.follow = False  # 用户在阅读: 锚定在该块头部, 不跳底部
                self.anchor = block.get("_line", 0)
                self.on_redraw()
            else:
                return NotImplemented

        return handler

    VIEW_LINES = 400  # 每帧只渲染锚点附近这么多行 (Window 的 create_content 是 O(总行数))

    @staticmethod
    def _block_key(b):
        kind = b["kind"]
        if kind == "text":
            return (len(b["ansi"]),)
        if kind == "tool":
            return (len(b["result"]), b["expanded"])
        if kind == "reasoning":
            return (b["label"], b["expanded"], len(b["text"]))
        if kind == "approval":
            return (b["answer"], b["selected"])
        return (b.get("text"),)

    @staticmethod
    def _slice_frags(frags, lo, hi):
        """取 fragments 中第 [lo, hi) 行 (按 \\n 计数)。"""
        out = []
        line = 0
        for f in frags:
            if line >= hi:
                break
            n = f[1].count("\n")
            if n == 0:
                if line >= lo:
                    out.append(f)
            elif line >= lo and line + n <= hi:
                out.append(f)
            elif line + n > lo:  # 部分交叠, 按行切开
                parts = f[1].split("\n")
                kept = [parts[i] for i in range(len(parts) - 1) if lo <= line + i < hi]
                if kept:
                    out.append((f[0], "\n".join(kept) + "\n") + tuple(f[2:]))
            line += n
        return out

    def fragments(self):
        entries = []
        total = 0
        for b in self.blocks:
            key = self._block_key(b)
            cached = self._ansi_cache.get(id(b))
            if cached is None or cached[0] != key:  # 逐帧重建 fragment 很贵, 块级缓存
                block_frags = []
                self._emit_block(b, block_frags)
                cached = (key, block_frags, sum(f[1].count("\n") for f in block_frags))
                self._ansi_cache[id(b)] = cached
            b["_line"] = total
            entries.append(cached)
            total += cached[2]
        self.total_lines = total

        # 视口窗口: follow 时取末尾, 否则以锚点为中心
        if self.follow or total == 0:
            target = max(total - 1, 0)
            lo = max(0, total - self.VIEW_LINES)
        else:
            target = min(max(self.anchor, 0), total - 1)
            lo = max(0, target - self.VIEW_LINES // 2)
        hi = min(total, lo + self.VIEW_LINES)

        out = []
        pos = 0
        for _, block_frags, n in entries:
            if pos + n > lo and pos < hi:
                out.extend(self._slice_frags(block_frags, lo - pos, hi - pos))
            pos += n

        # 光标锚点: Window 自动滚动保持它可见
        marker = ("[SetCursorPosition]", "")
        local = target - lo
        acc = 0
        for i, f in enumerate(out):
            if acc >= local:
                out.insert(i, marker)
                return out
            acc += f[1].count("\n")
        out.append(marker)
        return out

    def _emit_block(self, b, frags):
        if b["kind"] == "text":
            frags += to_formatted_text(ANSI(b["ansi"]))  # 缓存在 fragments() 的块级缓存
        elif b["kind"] == "user":
            for i, line in enumerate(b["text"].splitlines() or [""]):
                prefix = "❯ " if i == 0 else "  "
                frags += [("class:user", prefix), ("class:user.text", f" {line} "), ("", "\n")]
        elif b["kind"] == "approval":
            if b["answer"] is None:
                frags.append(("class:approval", f"⚠ {b['name']}  {_truncate_width(b['summary'], 100)}\n"))
                for i, (key, label) in enumerate(self.APPROVAL_OPTIONS):
                    def click(mouse_event, k=key):
                        if mouse_event.event_type == MouseEventType.MOUSE_UP:
                            self.answer_pending(k)
                        else:
                            return NotImplemented

                    cursor = "❯" if i == b["selected"] else " "
                    style = "class:approval.selected" if i == b["selected"] else "class:approval.option"
                    frags.append((style, f"  {cursor} {i + 1}. {label}  ", click))
                    frags.append(("", "\n"))
            else:
                verdict = {"y": "已允许", "a": "已允许 (不再询问)", "auto": "已开启自动审核", "n": "已拒绝"}.get(b["answer"], "已拒绝")
                frags.append(("class:dim", f"⚠ {b['name']}: {verdict}\n"))
        elif b["kind"] == "reasoning":
            h = self._toggle(b)
            arrow = "▾" if b["expanded"] else "▸"
            frags.append(("class:dim", f"{arrow} {b['label']}\n", h))
            if b["expanded"]:
                for line in b["text"].splitlines():
                    frags.append(("class:dim", f"  {line.expandtabs(4)}\n"))
        elif b["kind"] == "tool":
            h = self._toggle(b)
            indent = "  " * b["depth"]
            arrow = "▾" if b["expanded"] else "▸"
            frags += [
                ("class:dim", f"{indent}{arrow} ", h),
                ("class:tool.bullet", "● ", h),
                ("class:tool.name", b["name"] + " ", h),
                ("class:dim", _truncate_width(b["summary"], 120) + "\n", h),
            ]
            lines = [ln.expandtabs(4) for ln in b["result"].splitlines()] or [""]
            if b["expanded"]:
                for line in lines:
                    frags.append(("class:dim", f"{indent}    │ {line}\n"))
            else:
                more = f" +{len(lines) - 1} 行" if len(lines) > 1 else ""
                frags.append(("class:dim", f"{indent}    └ {_truncate_width(lines[0], 100)}{more}\n", h))


def _status_fragments(agent, running):
    effort = agent.llm.effective_effort()
    if getattr(agent.ui, "pending", None) is not None:
        hint = "  ·  等待选择: ↑↓+Enter / 数字键 / 点击"
    elif running[0]:
        hint = "  ·  运行中 (ctrl-c 中断)"
    else:
        hint = "  ·  点击 ▸ 展开 · 滚轮/PgUp/PgDn 滚动"
    frags = [
        ("class:status.model", f" {agent.llm.model}"),
        ("class:status", "  ·  "),
        ("class:status.effort", f"effort: {effort or '不支持'}"),
        ("class:status", "  ·  "),
        ("class:status.cwd", agent.cwd),
    ]
    used = agent.context_used()
    if used > 0:
        color = "class:status.effort" if used > 0.6 else "class:status"
        frags += [("class:status", "  ·  "), (color, f"ctx {used:.0%}")]
    frags.append(("class:status", hint))
    return frags


def run_app(agent, handle_command, banner="", input=None, output=None):
    """全屏 TUI 主循环: 上方可点击会话区, 下方输入框。agent.ui 必须是 TranscriptUI。"""
    ui = agent.ui
    running = [False]
    history = InMemoryHistory()

    # Window 只渲染可视区 (整屏虚拟渲染在长会话下每帧数秒); 滚动由 fragments 里的
    # [SetCursorPosition] 锚点驱动: Window 自动滚动保持锚点可见。
    # 滚轮不能走 Window 自带滚动 (内容是虚拟视口), 必须改锚点
    class _TranscriptControl(FormattedTextControl):
        def mouse_handler(self, mouse_event):
            if mouse_event.event_type == MouseEventType.SCROLL_UP:
                ui.scroll(-3)
            elif mouse_event.event_type == MouseEventType.SCROLL_DOWN:
                ui.scroll(3)
            else:
                return super().mouse_handler(mouse_event)

    transcript = Window(
        _TranscriptControl(ui.fragments, focusable=False),
        wrap_lines=True,
        right_margins=[ScrollbarMargin()],
    )

    def follow_bottom():
        ui.follow = True

    if banner:
        ui.ansi(banner)

    buf = Buffer(multiline=True, history=history, completer=SlashCompleter(agent), complete_while_typing=True)

    def submit(text):
        ui.user(text)
        follow_bottom()
        out = _capture_command(text)
        if out is False:
            app.exit()
            return
        if out is not None:  # 命令已处理
            return
        running[0] = True

        def work():
            try:
                agent.chat(text)
            except LLMError as e:
                ui.notice(f"错误: {e}")
            except KeyboardInterrupt:
                ui.notice("(已中断)")
            except Exception as e:  # 后台线程兜底, 避免静默挂掉
                ui.notice(f"内部错误: {type(e).__name__}: {e}")
            finally:
                running[0] = False
                follow_bottom()
                app.invalidate()

        threading.Thread(target=work, daemon=True).start()

    def _capture_command(text):
        """斜杠命令: 捕获其 print 输出进会话区。返回 None 表示不是命令。"""
        buf_out = io.StringIO()
        with contextlib.redirect_stdout(buf_out):
            handled = handle_command(agent, text)
        if handled is None:
            return None
        if buf_out.getvalue().strip():
            ui.ansi(buf_out.getvalue().rstrip("\n"))
        return handled

    kb = KeyBindings()
    approval_active = Condition(lambda: ui.pending is not None)

    @kb.add("enter", filter=~approval_active)
    def _(event):
        state = buf.complete_state
        if state and state.current_completion:
            buf.apply_completion(state.current_completion)
            return
        text = buf.text.strip()
        if not text:
            return
        if running[0]:
            ui.notice("任务运行中, 等它结束或 ctrl-c 中断")
            return
        buf.append_to_history()
        buf.reset()
        submit(text)

    @kb.add("escape", "enter")
    def _(event):
        buf.insert_text("\n")

    @kb.add("c-c")
    def _(event):
        if ui.pending is not None:
            ui.answer_pending("n")
        elif running[0]:
            agent.abort = True
            getattr(agent.llm, "abort", lambda: None)()  # 关闭连接, 立即打断阻塞的读
        else:
            event.app.exit()

    # 确认菜单: ↑↓ 移动, Enter 选中, 数字直选, Esc 拒绝, 也可鼠标点击
    def _move_selection(delta):
        if ui.pending is not None:
            n = len(ui.APPROVAL_OPTIONS)
            ui.pending["selected"] = (ui.pending["selected"] + delta) % n
            ui.on_change()

    @kb.add("up", filter=approval_active)
    def _(event):
        _move_selection(-1)

    @kb.add("down", filter=approval_active)
    def _(event):
        _move_selection(1)

    @kb.add("enter", filter=approval_active)
    def _(event):
        ui.answer_pending(ui.APPROVAL_OPTIONS[ui.pending["selected"]][0])

    @kb.add("escape", filter=approval_active, eager=True)
    def _(event):
        ui.answer_pending("n")

    for _i in range(len(TranscriptUI.APPROVAL_OPTIONS)):
        @kb.add(str(_i + 1), filter=approval_active)
        def _(event, _i=_i):
            ui.answer_pending(ui.APPROVAL_OPTIONS[_i][0])

    @kb.add("y", filter=approval_active)
    def _(event):
        ui.answer_pending("y")

    @kb.add("n", filter=approval_active)
    def _(event):
        ui.answer_pending("n")

    @kb.add("c-d", filter=Condition(lambda: not buf.text))
    def _(event):
        event.app.exit()

    @kb.add("pageup")
    def _(event):
        ui.scroll(-10)

    @kb.add("pagedown")
    def _(event):
        ui.scroll(10)

    control = BufferControl(
        buffer=buf,
        input_processors=[
            BeforeInput([("class:arrow", "❯ ")]),
            ConditionalProcessor(
                AfterInput([("class:placeholder", PLACEHOLDER)]),
                filter=Condition(lambda: not buf.text),
            ),
        ],
    )
    # 默认一行, 跟随内容行数扩展 (最多 8 行); 补全菜单浮层朝上开, 不用预留高度
    input_window = Window(
        control,
        wrap_lines=True,
        height=lambda: Dimension.exact(min(max(buf.document.line_count, 1), 8)),
    )
    root = FloatContainer(
        HSplit(
            [
                transcript,
                Frame(input_window),
                Window(FormattedTextControl(lambda: _status_fragments(agent, running)), height=1),
            ]
        ),
        floats=[Float(xcursor=True, ycursor=True, content=CompletionsMenu(max_height=8, scroll_offset=1))],
    )
    app = Application(
        layout=Layout(root, focused_element=input_window),
        key_bindings=kb,
        style=STYLE,
        full_screen=True,
        mouse_support=True,
        min_redraw_interval=0.03,  # 流式 delta 的重绘节流
        input=input,
        output=output,
    )
    ui.on_change = lambda: (follow_bottom(), app.invalidate())
    # 展开/折叠只重绘, 锚点已由 _toggle 定在被点击的块, 不跳底部
    ui.on_redraw = lambda: app.invalidate()
    follow_bottom()
    app.run()
