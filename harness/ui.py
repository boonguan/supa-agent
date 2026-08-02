import difflib


class C:
    RESET = "\033[0m"
    DIM = "\033[2m"
    BOLD = "\033[1m"
    CYAN = "\033[36m"
    BOLD_CYAN = "\033[1;36m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    MAGENTA = "\033[35m"
    RED = "\033[31m"
    BLUE = "\033[34m"


def tool_line(name, summary, depth=0):
    """opencode 风格工具调用行: ● tool 参数摘要"""
    indent = "  " * depth
    print(f"{indent}{C.GREEN}●{C.RESET} {C.BOLD}{name}{C.RESET} {C.DIM}{summary[:160]}{C.RESET}")


def result_preview(text, max_lines=4, depth=0):
    indent = "  " * depth
    lines = str(text).splitlines()
    for line in lines[:max_lines]:
        print(f"{indent}  {C.DIM}│ {line[:160]}{C.RESET}")
    if len(lines) > max_lines:
        print(f"{indent}  {C.DIM}│ … 共 {len(lines)} 行{C.RESET}")


def print_diff(old, new):
    diff = list(difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm="", n=2))
    for line in diff[2:]:  # 跳过 ---/+++ 头
        if line.startswith("+"):
            print(f"  {C.GREEN}{line}{C.RESET}")
        elif line.startswith("-"):
            print(f"  {C.RED}{line}{C.RESET}")
        else:
            print(f"  {C.DIM}{line}{C.RESET}")


TODO_MARKS = {
    "pending": ("☐", C.DIM),
    "in_progress": ("◐", C.YELLOW),
    "completed": ("☑", C.GREEN),
}


def print_todos(todos):
    for t in todos:
        mark, color = TODO_MARKS.get(t.get("status", "pending"), ("☐", C.DIM))
        print(f"  {color}{mark} {t.get('content', '')}{C.RESET}")
