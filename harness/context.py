"""项目记忆 (SUPA.md) 与 skills 发现, 组装进系统提示。"""
import datetime
import platform
import subprocess
from pathlib import Path

MEMORY_FILE = "SUPA.md"

SYSTEM_PROMPT_TEMPLATE = """你是 supa-agent, 一个基于 {model} 模型的 coding agent, 运行在用户的 shell 里。
你可以调用工具来查看、修改文件和执行命令。规则:
1. 先探索再动手, 用 list_dir / read_file / grep 了解现状。
2. 修改已有文件优先用 edit_file (精确替换), 新建或整体重写才用 write_file。
3. 多步骤任务先用 todo_write 列出计划, 每完成一步更新状态。
4. 独立的大块子任务可以用 task 派生子代理并行处理, 子代理返回结果摘要。
5. 用 bash 运行测试或验证你的修改。
6. 学到用户偏好或项目关键事实时用 remember 记下来。
7. 完成后用自然语言总结你做了什么。"""


def load_memory(cwd):
    p = Path(cwd) / MEMORY_FILE
    if p.exists():
        return p.read_text(errors="replace").strip()
    return ""


def append_memory(cwd, fact):
    p = Path(cwd) / MEMORY_FILE
    if not p.exists():
        p.write_text(f"# 项目记忆\n\n- {fact}\n", encoding="utf-8")
    else:
        with p.open("a", encoding="utf-8") as f:
            f.write(f"- {fact}\n")
    return str(p)


def _parse_frontmatter(text):
    """解析 SKILL.md 开头 --- 包围的 name/description。"""
    meta = {}
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return meta
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" in line:
            k, _, v = line.partition(":")
            meta[k.strip()] = v.strip()
    return meta


def discover_skills(cwd):
    """扫描 <cwd>/.supa/skills/*/SKILL.md 与 ~/.supa/skills/*/SKILL.md。"""
    skills = []
    seen = set()
    for root in (Path(cwd) / ".supa" / "skills", Path.home() / ".supa" / "skills"):
        if not root.is_dir():
            continue
        for f in sorted(root.glob("*/SKILL.md")):
            try:
                meta = _parse_frontmatter(f.read_text(errors="replace"))
            except OSError:
                continue
            name = meta.get("name", f.parent.name)
            if name in seen:
                continue
            seen.add(name)
            skills.append({"name": name, "description": meta.get("description", ""), "path": str(f)})
    return skills


def _git_info(cwd):
    try:
        r = subprocess.run(["git", "-C", cwd, "branch", "--show-current"], capture_output=True, text=True, timeout=3)
        if r.returncode != 0:
            return ""
        branch = r.stdout.strip() or "(detached HEAD)"
        s = subprocess.run(["git", "-C", cwd, "status", "--porcelain"], capture_output=True, text=True, timeout=3)
        dirty = len(s.stdout.splitlines())
        return f"git 仓库, 分支 {branch}, {dirty} 个文件有未提交改动"
    except Exception:
        return ""


def _env_block(cwd):
    lines = [
        f"- 平台: {platform.system()} ({platform.machine()})",
        f"- 日期: {datetime.date.today().isoformat()}",
        f"- 工作目录: {cwd}",
    ]
    git = _git_info(cwd)
    lines.append(f"- {git}" if git else "- 不是 git 仓库")
    return "# 环境\n" + "\n".join(lines)


def build_system_prompt(model, cwd):
    parts = [SYSTEM_PROMPT_TEMPLATE.format(model=model), _env_block(cwd)]
    memory = load_memory(cwd)
    if memory:
        parts.append(f"# 项目记忆 ({MEMORY_FILE})\n{memory}")
    skills = discover_skills(cwd)
    if skills:
        listing = "\n".join(f"- {s['name']}: {s['description']} (完整指令: read_file {s['path']})" for s in skills)
        parts.append(f"# 可用 skills\n任务匹配某个 skill 时, 先 read_file 其 SKILL.md 获取完整指令再执行:\n{listing}")
    return "\n\n".join(parts)
