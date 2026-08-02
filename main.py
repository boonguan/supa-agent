import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from harness.agent import Agent
from harness.context import discover_skills, load_memory
from harness.llm import LLM, LLMError, SUPPORTED_MODELS
from harness.ui import C, print_todos

try:
    from harness.tui import create_session, prompt_line

    TUI_AVAILABLE = True
except ImportError:
    TUI_AVAILABLE = False

HELP = """命令:
  /exit             退出
  /reset            清空对话历史
  /model [名称]     查看或切换模型 (如 /model deepseek-v4-pro)
  /effort [级别]    查看或切换推理强度 (low / medium / high / max)
  /cwd <路径>       切换工作目录
  /skills           列出可用 skills (.supa/skills/*/SKILL.md)
  /memory           查看项目记忆 (SUPA.md)
  /todos            查看当前任务清单
  /help             显示帮助
其余输入都会作为任务发给 agent"""

VALID_EFFORTS = ("low", "medium", "high", "max")


def handle_command(agent, line):
    if line in ("/exit", "/quit", "/q"):
        return False
    if line == "/help":
        print(HELP)
    elif line == "/reset":
        agent.reset()
        print("历史已清空")
    elif line == "/model" or line.startswith("/model "):
        name = line[6:].strip()
        if name:
            if "deepseek" in agent.llm.base_url and name not in SUPPORTED_MODELS:
                print(f"{C.YELLOW}提示: DeepSeek 支持 {', '.join(SUPPORTED_MODELS)}, 你设置的是 {name}{C.RESET}")
            agent.set_model(name)
            print(f"{C.GREEN}模型已切换: {name}{C.RESET}")
        else:
            print(f"当前模型: {agent.llm.model}")
    elif line == "/effort" or line.startswith("/effort "):
        effort = line[7:].strip()
        if effort:
            effort = effort.lower()
            if effort not in VALID_EFFORTS:
                print(f"无效值: {effort} (可选: {', '.join(VALID_EFFORTS)})")
            else:
                agent.llm.effort = effort
                print(f"{C.GREEN}推理强度已切换: {effort}{C.RESET}")
        else:
            print(f"当前推理强度: {agent.llm.effort} (可选: {', '.join(VALID_EFFORTS)})")
    elif line.startswith("/cwd "):
        p = Path(line[5:].strip()).expanduser()
        if p.exists() and p.is_dir():
            agent.cwd = str(p.resolve())
            agent.refresh_system()
            print(f"已切换: {agent.cwd}")
        else:
            print(f"目录不存在: {p}")
    elif line == "/skills":
        skills = discover_skills(agent.cwd)
        if not skills:
            print("没有发现 skills, 在 .supa/skills/<名称>/SKILL.md 添加")
        for s in skills:
            print(f"  {C.GREEN}{s['name']}{C.RESET}  {s['description']}  {C.DIM}{s['path']}{C.RESET}")
    elif line == "/memory":
        memory = load_memory(agent.cwd)
        print(memory if memory else f"暂无项目记忆, agent 会通过 remember 工具写入 {agent.cwd}/SUPA.md")
    elif line == "/todos":
        if agent.todos:
            print_todos(agent.todos)
        else:
            print("当前没有任务清单")
    else:
        return None
    return True


def _model_hint(error_text):
    import re

    m = re.search(r"supported API model names are ([\w.-]+) or ([\w.-]+)", error_text)
    if m:
        return ", ".join(m.groups())
    return None


def repl(agent):
    print(f"{C.BOLD_CYAN}supa-agent{C.RESET}  ·  {C.GREEN}model: {agent.llm.model}{C.RESET}  ·  {C.YELLOW}effort: {agent.llm.effort}{C.RESET}  ·  {C.CYAN}cwd: {agent.cwd}{C.RESET}")
    print("输入 / 查看可用命令")
    session = create_session() if TUI_AVAILABLE else None
    while True:
        try:
            if TUI_AVAILABLE:
                line = prompt_line(session, agent).strip()
            else:
                line = input(f"{agent.cwd} > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nbye")
            break
        if not line:
            continue
        handled = handle_command(agent, line)
        if handled is None:
            try:
                agent.chat(line)
            except LLMError as e:
                print(f"{C.YELLOW}错误: {e}{C.RESET}")
                hint = _model_hint(str(e))
                if hint:
                    print(f"{C.DIM}可用模型: {hint}{C.RESET}")
                print(f"{C.DIM}用 /model 查看或切换当前模型{C.RESET}")
            except KeyboardInterrupt:
                print("\n(已中断)")
        elif not handled:
            break


def main():
    ap = argparse.ArgumentParser(description="zero-dependency coding agent harness")
    ap.add_argument("task", nargs="*", help="任务描述 (省略则进入交互模式)")
    ap.add_argument("-d", "--dir", default=".", help="工作目录 (默认当前目录)")
    ap.add_argument("--base-url", default=None, help="API base url, 默认取 LLM_BASE_URL")
    ap.add_argument("--api-key", default=None, help="API key, 默认取 LLM_API_KEY")
    ap.add_argument("--model", default=None, help="模型名, 默认取 LLM_MODEL")
    ap.add_argument("--effort", default=None, help="推理强度, 默认取 LLM_EFFORT")
    args = ap.parse_args()

    try:
        llm = LLM(base_url=args.base_url, api_key=args.api_key, model=args.model)
        if args.effort:
            llm.effort = args.effort.lower()
    except LLMError as e:
        print(f"错误: {e}", file=sys.stderr)
        print("需要设置环境变量 LLM_API_KEY, 可选 LLM_BASE_URL / LLM_MODEL, 详见 README", file=sys.stderr)
        sys.exit(1)

    agent = Agent(llm, cwd=str(Path(args.dir).resolve()))
    if args.task:
        try:
            agent.run(" ".join(args.task))
        except LLMError as e:
            print(f"错误: {e}", file=sys.stderr)
            hint = _model_hint(str(e))
            if hint:
                print(f"可用模型: {hint}", file=sys.stderr)
            sys.exit(1)
    else:
        repl(agent)


if __name__ == "__main__":
    main()
