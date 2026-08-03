import difflib
import re
import shutil
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


def _truncate_width(s, maxw):
    """按显示宽度截断 (CJK 记 2 列), 超出加 …"""
    w = 0
    for i, ch in enumerate(s):
        w += 2 if unicodedata.east_asian_width(ch) in "WF" else 1
        if w > maxw:
            return s[:i] + "…"
    return s


def tool_line(name, summary, depth=0):
    """opencode 风格工具调用行: ● tool 参数摘要 (单行截断)"""
    indent = "  " * depth
    width = shutil.get_terminal_size().columns - len(indent) - len(name) - 4
    summary = " ".join(summary.split())  # 多行命令压成一行
    print(f"{indent}{C.GREEN}●{C.RESET} {C.BOLD}{name}{C.RESET} {C.DIM}{_truncate_width(summary, max(width, 20))}{C.RESET}")


def result_collapsed(text, depth=0):
    """默认折叠: 结果首行 + 剩余行数, /output 可回看全文"""
    indent = "  " * depth
    lines = str(text).splitlines() or [""]
    width = shutil.get_terminal_size().columns - len(indent) - 12
    head = _truncate_width(lines[0], max(width, 20))
    more = f" +{len(lines) - 1} 行" if len(lines) > 1 else ""
    print(f"{indent}  {C.DIM}└ {head}{more}{C.RESET}")


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
    """按行流式把 Markdown 渲染成 ANSI: 标题/加粗/行内代码/代码块/列表/表格对齐。
    printer 每次收到一行渲染好的 ANSI 文本 (不含换行)。"""

    def __init__(self, printer=print):
        self._p = printer
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
            self._p(f"  {C.CYAN}{line}{C.RESET}")
            return
        if stripped.startswith("|") and stripped.endswith("|") and len(stripped) > 1:
            self.table.append(stripped)
            return
        self._flush_table()
        m = re.match(r"^(#{1,6})\s+(.*)", stripped)
        if m:
            self._p(f"{C.BOLD_CYAN}{m.group(2)}{C.RESET}")
            return
        if re.match(r"^[-*_]{3,}$", stripped):
            self._p(f"{C.DIM}{'─' * 40}{C.RESET}")
            return
        m = re.match(r"^(\s*)[-*]\s+(.*)", line)
        if m:
            self._p(f"{m.group(1)}• {_inline(m.group(2))}")
            return
        if stripped.startswith("> "):
            self._p(f"{C.DIM}│ {_inline(stripped[2:])}{C.RESET}")
            return
        self._p(_inline(line))

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
            self._p(f"{C.BOLD if n == 0 else ''}{body}{C.RESET}")
            if n == 0:
                self._p(f"{C.DIM}{'─' * min(sum(widths) + 3 * (ncols - 1), 100)}{C.RESET}")


def render_markdown(text):
    md = MdStream()
    md.feed(text)
    md.close()


class ConsoleUI:
    """默认 UI: 直接 print 到终端 (一次性模式 / 无 prompt_toolkit 降级 / 测试)。
    Agent 通过这组回调输出; TUI 模式换成 TranscriptUI (harness/tui.py)。"""

    def __init__(self):
        self.verbose = False  # 工具结果: 折叠单行 / 多行预览
        self.show_reasoning = False  # 思维链: 折叠进度行 / 原文
        self._md = None

    def on_reasoning(self, delta, est_tokens, depth):
        if depth:
            return
        if self.show_reasoning:
            print(f"{C.DIM}{delta}{C.RESET}", end="", flush=True)
        else:
            print(f"\r{C.DIM}✱ 思考中… ~{est_tokens} tokens{C.RESET}", end="", flush=True)

    def end_reasoning(self, label, text, depth):
        if depth:
            return
        if self.show_reasoning:
            print()
        else:
            print(f"\r{C.DIM}✱ 已思考 ({label}){' ' * 12}{C.RESET}")

    def on_content(self, delta, depth):
        if depth:
            return
        if self._md is None:
            self._md = MdStream()
        self._md.feed(delta)

    def end_content(self, depth):
        if self._md is not None:
            self._md.close()
            self._md = None

    def tool_call(self, name, summary, depth):
        tool_line(name, summary, depth)

    def tool_result(self, name, result, depth):
        if name in ("todo_write", "edit_file"):  # 自带渲染 (todos/diff)
            return
        if self.verbose:
            result_preview(result, depth=depth)
        else:
            result_collapsed(result, depth=depth)

    def diff(self, old, new):
        print_diff(old, new)

    def todos(self, todos):
        print_todos(todos)

    def notice(self, text):
        print(f"{C.DIM}{text}{C.RESET}")

    def confirm(self, name, summary, depth):
        """询问是否允许执行修改类操作。返回 y / a / auto / n。"""
        print(f"{C.YELLOW}⚠ {name}  {summary[:120]}{C.RESET}")
        print(f"{C.YELLOW}  1. 允许  2. 允许且不再询问  3. 开启自动审核  4. 拒绝{C.RESET}")
        mapping = {"1": "y", "y": "y", "2": "a", "a": "a", "3": "auto", "auto": "auto", "4": "n", "n": "n"}
        try:
            return mapping.get(input("> ").strip().lower(), "n")
        except (EOFError, KeyboardInterrupt):
            return "n"
