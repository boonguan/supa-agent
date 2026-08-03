import subprocess
from pathlib import Path

from . import context

TOOLS = []


def tool(name, description, parameters, readonly=False):
    def deco(fn):
        fn.name = name
        fn.description = description
        fn.parameters = parameters
        fn.readonly = readonly
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
def run_bash(agent, command):
    try:
        proc = subprocess.run(command, shell=True, cwd=agent.cwd, capture_output=True, text=True, timeout=120)
        out = (proc.stdout or "") + (proc.stderr or "")
        return _truncate(out if out.strip() else "(无输出, 退出码 {})".format(proc.returncode))
    except subprocess.TimeoutExpired:
        return "命令执行超时 (120s)"


@tool(
    "read_file",
    "读取文件内容, 带行号。大文件用 offset 分页读取。",
    {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件路径, 相对或绝对"},
            "offset": {"type": "integer", "description": "从第几行开始读 (1 起), 默认 1"},
            "limit": {"type": "integer", "description": "最多读取的行数, 默认 500"},
        },
        "required": ["path"],
    },
    readonly=True,
)
def read_file(agent, path, offset=1, limit=500):
    p = _resolve(path, agent.cwd)
    if not p.exists():
        return f"文件不存在: {p}"
    if p.is_dir():
        return f"{p} 是目录, 请用 list_dir"
    lines = p.read_text(errors="replace").splitlines()
    start = max(offset - 1, 0)
    shown = lines[start:start + limit]
    if not shown:
        return f"offset 超出范围 (共 {len(lines)} 行)"
    result = "\n".join(f"{start + i + 1}: {line}" for i, line in enumerate(shown))
    if start + limit < len(lines):
        result += f"\n...(共 {len(lines)} 行, 显示 {start + 1}-{start + len(shown)} 行, 继续读用 offset={start + len(shown) + 1})"
    return result


@tool(
    "write_file",
    "写入或覆盖文件内容, 父目录不存在会自动创建。新建文件或整体重写时使用; 修改已有文件优先用 edit_file。",
    {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件路径, 相对或绝对"},
            "content": {"type": "string", "description": "完整文件内容"},
        },
        "required": ["path", "content"],
    },
)
def write_file(agent, path, content):
    p = _resolve(path, agent.cwd)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"已写入 {p} ({len(content)} 字符)"


@tool(
    "edit_file",
    "精确字符串替换修改文件: old 必须在文件中唯一出现, 会被 new 替换。修改已有文件的首选方式。",
    {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件路径, 相对或绝对"},
            "old": {"type": "string", "description": "要替换的原文, 必须与文件内容完全一致且唯一"},
            "new": {"type": "string", "description": "替换后的新文本"},
        },
        "required": ["path", "old", "new"],
    },
)
def edit_file(agent, path, old, new):
    p = _resolve(path, agent.cwd)
    if not p.exists():
        return f"文件不存在: {p}"
    text = p.read_text(errors="replace")
    count = text.count(old)
    if count == 0:
        return "未找到要替换的原文, 请 read_file 确认内容后重试"
    if count > 1:
        return f"原文出现 {count} 次, 不唯一, 请提供更多上下文"
    p.write_text(text.replace(old, new, 1), encoding="utf-8")
    agent.ui.diff(old, new)
    return f"已修改 {p}"


@tool(
    "list_dir",
    "列出目录内容。",
    {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "目录路径, 默认当前工作目录"},
        },
    },
    readonly=True,
)
def list_dir(agent, path="."):
    p = _resolve(path, agent.cwd)
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
    readonly=True,
)
def grep(agent, pattern, path=".", include=None):
    import re
    import shutil

    p = _resolve(path, agent.cwd)
    if not p.exists():
        return f"目录不存在: {p}"
    if shutil.which("rg"):  # ripgrep 快几个数量级, 优先用
        cmd = ["rg", "-n", "--no-heading", "--max-count", "50", "-e", pattern]
        if include:
            cmd += ["-g", include]
        cmd.append(str(p))
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        except subprocess.TimeoutExpired:
            return "搜索超时 (30s)"
        if proc.returncode == 2:
            return f"rg 出错: {proc.stderr[:300]}"
        lines = proc.stdout.splitlines()
        if len(lines) > 200:
            lines = lines[:200] + ["...(匹配过多, 已截断)"]
        return "\n".join(line[:400] for line in lines) if lines else "(无匹配)"

    # fallback: 纯 Python, 跳过大目录
    skip_dirs = {".git", "node_modules", "venv", ".venv", "__pycache__", ".cache", "dist", "build"}
    regex = re.compile(pattern)
    matches = []
    if p.is_file():
        targets = [p]
    else:
        targets = (
            f for f in p.rglob("*")
            if f.is_file() and not any(part in skip_dirs for part in f.relative_to(p).parts)
        )
    for f in targets:
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


@tool(
    "todo_write",
    "维护当前任务清单, 每次传完整清单覆盖旧的。多步骤任务开始时列出计划, 每完成一步更新状态。",
    {
        "type": "object",
        "properties": {
            "todos": {
                "type": "array",
                "description": "完整任务清单",
                "items": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string", "description": "任务描述"},
                        "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]},
                    },
                    "required": ["content", "status"],
                },
            }
        },
        "required": ["todos"],
    },
    readonly=True,
)
def todo_write(agent, todos):
    agent.todos = todos
    agent.ui.todos(todos)
    marks = {"pending": "[ ]", "in_progress": "[~]", "completed": "[x]"}
    return "\n".join(f"{marks.get(t['status'], '[ ]')} {t['content']}" for t in todos) or "(清单已清空)"


@tool(
    "task",
    "派生一个子代理独立完成子任务并返回结果摘要。适合独立的大块工作 (研究一个模块、批量修改)。子代理从空白上下文开始, prompt 必须包含全部必要背景与验收标准。",
    {
        "type": "object",
        "properties": {
            "description": {"type": "string", "description": "子任务简述, 几个词, 展示给用户"},
            "prompt": {"type": "string", "description": "给子代理的完整任务说明"},
        },
        "required": ["description", "prompt"],
    },
    readonly=True,
)
def task(agent, description, prompt):
    if agent.depth >= 1:
        return "子代理不能再派生子代理, 请直接完成"
    from .agent import Agent  # 延迟导入避免循环依赖

    sub = Agent(agent.llm, agent.cwd, depth=agent.depth + 1, ui=agent.ui, policy=agent.policy)
    result = sub.chat(prompt)
    return result or "(子代理未返回结果)"


@tool(
    "remember",
    "把用户偏好或项目关键事实追加到项目记忆 (SUPA.md), 之后每次会话都会自动加载。",
    {
        "type": "object",
        "properties": {
            "fact": {"type": "string", "description": "要记住的一条事实, 简洁一行"},
        },
        "required": ["fact"],
    },
    readonly=True,
)
def remember(agent, fact):
    path = context.append_memory(agent.cwd, fact)
    return f"已记入 {path}"
