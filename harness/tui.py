import shutil

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style

from .llm import SUPPORTED_MODELS

COMMANDS = ["/exit", "/reset", "/model", "/effort", "/cwd", "/skills", "/memory", "/todos", "/help"]

# opencode 风格: 边框输入框 + 低调前景色状态栏 (不用背景色块)
STYLE = Style.from_dict(
    {
        "border": "#4b5563",
        "arrow": "bold #34d399",
        "placeholder": "italic #6b7280",
        "completion-menu.completion": "bg:#1f2937 #d1d5db",
        "completion-menu.completion.current": "bg:#0ea5e9 #ffffff",
        "bottom-toolbar": "noreverse bg:default #6b7280",
        "bottom-toolbar.model": "noreverse bg:default #34d399",
        "bottom-toolbar.effort": "noreverse bg:default #fbbf24",
        "bottom-toolbar.cwd": "noreverse bg:default #93c5fd",
    }
)

DIM = "\033[2;38;5;240m"
RESET = "\033[0m"


class SlashCompleter(Completer):
    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if text.startswith("/model "):
            prefix = text[len("/model "):]
            for model in SUPPORTED_MODELS:
                if model.startswith(prefix):
                    yield Completion(model, start_position=-len(prefix))
            return
        if text.startswith("/"):
            for cmd in COMMANDS:
                if cmd.startswith(text):
                    yield Completion(cmd, start_position=-len(text))


def build_bindings():
    kb = KeyBindings()

    @kb.add("enter")
    def _(event):
        event.current_buffer.validate_and_handle()

    @kb.add("escape", "enter")
    def _(event):
        event.current_buffer.insert_text("\n")

    return kb


def create_session(**kwargs):
    return PromptSession(
        history=InMemoryHistory(),
        key_bindings=build_bindings(),
        completer=SlashCompleter(),
        complete_while_typing=True,
        style=STYLE,
        **kwargs,
    )


def _width():
    return shutil.get_terminal_size().columns


def prompt_line(session, agent):
    w = _width()
    print(f"{DIM}╭{'─' * (w - 2)}╮{RESET}")
    try:
        text = session.prompt(
            [("class:border", "│ "), ("class:arrow", "❯ ")],
            placeholder=[("class:placeholder", "输入任务, / 查看命令 · Enter 发送 · Alt+Enter 换行")],
            prompt_continuation=[("class:border", "│ "), ("", "  ")],
            bottom_toolbar=lambda: [
                ("class:bottom-toolbar.model", f" {agent.llm.model}"),
                ("class:bottom-toolbar", "  ·  "),
                ("class:bottom-toolbar.effort", f"effort: {agent.llm.effort}"),
                ("class:bottom-toolbar", "  ·  "),
                ("class:bottom-toolbar.cwd", agent.cwd),
            ],
        )
    finally:
        print(f"{DIM}╰{'─' * (w - 2)}╯{RESET}")
    return text
