from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style

COMMANDS = ["/exit", "/reset", "/model", "/effort", "/cwd", "/help"]

STYLE = Style.from_dict(
    {
        "prompt": "bg:#d1d5db #111827 bold",
        "buffer": "bg:#d1d5db #111827",
        "completion-menu.completion": "bg:#67e8f9 #111827",
        "completion-menu.completion.current": "bg:#0ea5e9 #ffffff",
        "model": "bold #86efac",
        "effort": "#fbbf24",
        "cwd": "#a5b4fc",
        "sep": "#6b7280",
    }
)


class SlashCompleter(Completer):
    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
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


def create_session():
    return PromptSession(
        history=InMemoryHistory(),
        key_bindings=build_bindings(),
        completer=SlashCompleter(),
        complete_while_typing=True,
        style=STYLE,
    )


def prompt_line(session, agent):
    return session.prompt(
        [("class:prompt", "> ")],
        bottom_toolbar=lambda: [
            ("class:model", f"model: {agent.llm.model}"),
            ("class:sep", "  ·  "),
            ("class:effort", f"effort: {agent.llm.effort}"),
            ("class:sep", "  ·  "),
            ("class:cwd", f"cwd: {agent.cwd}"),
        ],
    )
