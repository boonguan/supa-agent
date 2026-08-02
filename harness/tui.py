from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.filters import Condition
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Float, FloatContainer, HSplit, Layout, Window
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.menus import CompletionsMenu
from prompt_toolkit.layout.processors import AfterInput, BeforeInput, ConditionalProcessor
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import Frame
from prompt_toolkit.widgets import base as _widgets_base

from .llm import SUPPORTED_MODELS, supported_efforts

# Frame 默认直角边框, 改成 opencode 的圆角
_widgets_base.Border.TOP_LEFT = "╭"
_widgets_base.Border.TOP_RIGHT = "╮"
_widgets_base.Border.BOTTOM_LEFT = "╰"
_widgets_base.Border.BOTTOM_RIGHT = "╯"

COMMANDS = ["/exit", "/reset", "/model", "/effort", "/cwd", "/skills", "/memory", "/todos", "/help"]

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


class Session:
    """跨轮共享历史与 (测试用) 输入输出。"""

    def __init__(self, input=None, output=None):
        self.history = InMemoryHistory()
        self.input = input
        self.output = output


def create_session(input=None, output=None):
    return Session(input=input, output=output)


def _status_fragments(agent):
    effort = agent.llm.effective_effort()
    frags = [
        ("class:status.model", f" {agent.llm.model}"),
        ("class:status", "  ·  "),
        ("class:status.effort", f"effort: {effort or '不支持'}"),
        ("class:status", "  ·  "),
        ("class:status.cwd", agent.cwd),
    ]
    return frags


def prompt_line(session, agent):
    buf = Buffer(
        multiline=True,
        history=session.history,
        completer=SlashCompleter(agent),
        complete_while_typing=True,
    )

    kb = KeyBindings()

    @kb.add("enter")
    def _(event):
        state = buf.complete_state
        if state and state.current_completion:
            buf.apply_completion(state.current_completion)
            return
        buf.append_to_history()
        event.app.exit(result=buf.text)

    @kb.add("escape", "enter")
    def _(event):
        buf.insert_text("\n")

    @kb.add("c-c")
    def _(event):
        event.app.exit(exception=KeyboardInterrupt())

    @kb.add("c-d", filter=Condition(lambda: not buf.text))
    def _(event):
        event.app.exit(exception=EOFError())

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
    # 补全菜单展开时撑高输入区, 给浮层留位置
    input_window = Window(
        control,
        wrap_lines=True,
        height=lambda: Dimension(min=8) if buf.complete_state else None,
    )
    root = FloatContainer(
        HSplit(
            [
                Frame(input_window),
                Window(FormattedTextControl(lambda: _status_fragments(agent)), height=1),
            ]
        ),
        floats=[Float(xcursor=True, ycursor=True, content=CompletionsMenu(max_height=8, scroll_offset=1))],
    )
    app = Application(
        layout=Layout(root, focused_element=input_window),
        key_bindings=kb,
        style=STYLE,
        input=session.input,
        output=session.output,
    )
    return app.run()
