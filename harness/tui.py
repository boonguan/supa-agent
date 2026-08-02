from prompt_toolkit import PromptSession
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style

STYLE = Style.from_dict(
    {
        "prompt": "bold #67e8f9",
        "model": "bold #86efac",
        "cwd": "#a5b4fc",
        "sep": "#6b7280",
    }
)


def build_bindings():
    kb = KeyBindings()

    @kb.add("enter")
    def _(event):
        event.current_buffer.validate_and_handle()

    @kb.add("escape", "enter")
    def _(event):
        event.current_buffer.insert_text("\n")

    return kb


def create_session():
    return PromptSession(
        history=InMemoryHistory(),
        key_bindings=build_bindings(),
        style=STYLE,
        complete_while_typing=False,
    )


def prompt_line(session, agent):
    return session.prompt(
        [("class:prompt", "> ")],
        bottom_toolbar=lambda: [
            ("class:model", f"model: {agent.llm.model}"),
            ("class:sep", "  ·  "),
            ("class:cwd", f"cwd: {agent.cwd}"),
            ("class:sep", "  ·  Enter 发送 / Alt+Enter 换行"),
        ],
    )
