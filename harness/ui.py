import difflib
import re
import unicodedata


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


def _display_width(s):
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def _inline(s):
    s = re.sub(r"\*\*(.+?)\*\*", f"{C.BOLD}\\1{C.RESET}", s)
    s = re.sub(r"`([^`]+)`", f"{C.CYAN}\\1{C.RESET}", s)
    s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", f"\\1 {C.DIM}(\\2){C.RESET}", s)
    return s


def _plain(s):
    """去掉行内标记, 用于计算可见宽度。"""
    return re.sub(r"\*\*(.+?)\*\*", r"\1", re.sub(r"`([^`]+)`", r"\1", s))


class MdStream:
    """按行流式把 Markdown 渲染成 ANSI: 标题/加粗/行内代码/代码块/列表/表格对齐。"""

    def __init__(self):
        self.buf = ""
        self.table = []
        self.in_code = False

    def feed(self, text):
        self.buf += text
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            self._line(line)

    def close(self):
        if self.buf:
            self._line(self.buf)
            self.buf = ""
        self._flush_table()

    def _line(self, line):
        stripped = line.strip()
        if stripped.startswith("```"):
            self._flush_table()
            self.in_code = not self.in_code
            return
        if self.in_code:
            print(f"  {C.CYAN}{line}{C.RESET}")
            return
        if stripped.startswith("|") and stripped.endswith("|") and len(stripped) > 1:
            self.table.append(stripped)
            return
        self._flush_table()
        m = re.match(r"^(#{1,6})\s+(.*)", stripped)
        if m:
            print(f"{C.BOLD_CYAN}{m.group(2)}{C.RESET}")
            return
        if re.match(r"^[-*_]{3,}$", stripped):
            print(f"{C.DIM}{'─' * 40}{C.RESET}")
            return
        m = re.match(r"^(\s*)[-*]\s+(.*)", line)
        if m:
            print(f"{m.group(1)}• {_inline(m.group(2))}")
            return
        if stripped.startswith("> "):
            print(f"{C.DIM}│ {_inline(stripped[2:])}{C.RESET}")
            return
        print(_inline(line))

    def _flush_table(self):
        rows = [
            [cell.strip() for cell in r.strip("|").split("|")]
            for r in self.table
            if not re.match(r"^[|\s:-]+$", r)  # 跳过 |---|---| 分隔行
        ]
        self.table = []
        if not rows:
            return
        ncols = max(len(r) for r in rows)
        widths = [max(_display_width(_plain(r[i])) if i < len(r) else 0 for r in rows) for i in range(ncols)]
        for n, r in enumerate(rows):
            cells = [
                _inline(r[i] if i < len(r) else "") + " " * (widths[i] - _display_width(_plain(r[i] if i < len(r) else "")))
                for i in range(ncols)
            ]
            body = f" {C.DIM}│{C.RESET} ".join(cells)
            print(f"{C.BOLD if n == 0 else ''}{body}{C.RESET}")
            if n == 0:
                print(f"{C.DIM}{'─' * min(sum(widths) + 3 * (ncols - 1), 100)}{C.RESET}")


def render_markdown(text):
    md = MdStream()
    md.feed(text)
    md.close()
