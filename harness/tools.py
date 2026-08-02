import subprocess
from pathlib import Path

TOOLS = []


def tool(name, description, parameters):
    def deco(fn):
        fn.name = name
        fn.description = description
        fn.parameters = parameters
        TOOLS.append(fn)
        return fn

    return deco


def _truncate(text, limit=20000):
    text = str(text)
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...(输出过长, 已截断, 共 {len(text)} 字符)"


def _resolve(path, cwd):
    p = Path(path)
    return p if p.is_absolute() else Path(cwd) / p


@tool(
    "bash",
    "执行任意 shell 命令并返回 stdout/stderr。用于查看文件、运行测试、安装依赖、启动服务等。",
    {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "要执行的 shell 命令"}
        },
        "required": ["command"],
    },
)
def run_bash(cwd, command):
    try:
        proc = subprocess.run(command, shell=True, cwd=cwd, capture_output=True, text=True, timeout=120)
        out = (proc.stdout or "") + (proc.stderr or "")
        return _truncate(out if out.strip() else "(无输出, 退出码 {})".format(proc.returncode))
    except subprocess.TimeoutExpired:
        return "命令执行超时 (120s)"


@tool(
    "read_file",
    "读取文件内容。",
    {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件路径, 相对或绝对"},
            "limit": {"type": "integer", "description": "最多读取的行数, 默认 500"},
        },
        "required": ["path"],
    },
)
def read_file(cwd, path, limit=500):
    p = _resolve(path, cwd)
    if not p.exists():
        return f"文件不存在: {p}"
    if p.is_dir():
        return f"{p} 是目录, 请用 list_dir"
    lines = p.read_text(errors="replace").splitlines()
    shown = lines[:limit]
    result = "\n".join(f"{i + 1}: {line}" for i, line in enumerate(shown))
    if len(lines) > limit:
        result += f"\n...(共 {len(lines)} 行, 只显示前 {limit} 行)"
    return result


@tool(
    "write_file",
    "写入或覆盖文件内容, 父目录不存在会自动创建。",
    {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件路径, 相对或绝对"},
            "content": {"type": "string", "description": "完整文件内容"},
        },
        "required": ["path", "content"],
    },
)
def write_file(cwd, path, content):
    p = _resolve(path, cwd)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"已写入 {p} ({len(content)} 字符)"


@tool(
    "list_dir",
    "列出目录内容。",
    {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "目录路径, 默认当前工作目录"},
        },
    },
)
def list_dir(cwd, path="."):
    p = _resolve(path, cwd)
    if not p.exists() or not p.is_dir():
        return f"目录不存在: {p}"
    entries = []
    for child in sorted(p.iterdir()):
        suffix = "/" if child.is_dir() else ""
        entries.append(child.name + suffix)
    return "\n".join(entries) if entries else "(空目录)"


@tool(
    "grep",
    "在目录中按正则搜索文件内容, 返回匹配行。",
    {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "正则表达式"},
            "path": {"type": "string", "description": "搜索的目录, 默认当前工作目录"},
            "include": {"type": "string", "description": "只匹配此 glob, 如 *.py"},
        },
        "required": ["pattern"],
    },
)
def grep(cwd, pattern, path=".", include=None):
    import re

    p = _resolve(path, cwd)
    if not p.exists():
        return f"目录不存在: {p}"
    regex = re.compile(pattern)
    matches = []
    targets = [p] if p.is_file() else list(p.rglob("*"))
    for f in targets:
        if not f.is_file():
            continue
        if include and not f.match(include):
            continue
        try:
            text = f.read_text(errors="replace")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if regex.search(line):
                matches.append(f"{f}:{i}: {line[:300]}")
                if len(matches) >= 200:
                    matches.append("...(匹配过多, 已截断)")
                    return "\n".join(matches)
    return "\n".join(matches) if matches else "(无匹配)"
