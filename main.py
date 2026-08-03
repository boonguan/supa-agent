import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from harness.agent import Agent
from harness.context import discover_skills, load_memory
from harness.llm import LLM, LLMError, SUPPORTED_MODELS, supported_efforts
from harness.policy import Policy
from harness.session import list_sessions
from harness.session import load as load_session
from harness.ui import C, print_todos

try:
    from harness.tui import TranscriptUI, run_app

    TUI_AVAILABLE = True
except ImportError:
    TUI_AVAILABLE = False

HELP = """命令:
  /exit             退出
  /reset            清空对话历史
  /model [名称]     查看或切换模型 (如 /model deepseek-v4-pro)
  /effort [级别]    查看或切换推理强度 (档位随模型, deepseek: none/low/high/max)
  /cwd <路径>       切换工作目录
  /skills           列出可用 skills (.supa/skills/*/SKILL.md)
  /memory           查看项目记忆 (SUPA.md)
  /todos            查看当前任务清单
  /reasoning        展开/折叠思维链显示 (默认折叠为进度行)
  /verbose          切换工具结果显示: 折叠单行 (默认) / 多行预览
  /output [n]       查看倒数第 n 条工具调用的完整输出 (默认最近一条)
  /compact          手动压缩对话历史 (上下文超 70% 会自动压缩)
  /sessions         列出最近保存的会话
  /resume [id]      恢复会话 (默认最近一个), 也可启动时 supa --resume
  /help             显示帮助
其余输入都会作为任务发给 agent"""


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
            effective = agent.llm.effective_effort()
            if effective and effective != agent.llm.effort:
                print(f"{C.YELLOW}推理强度 {agent.llm.effort} 超出该模型上限, 实际使用 {effective}{C.RESET}")
            elif not effective:
                print(f"{C.DIM}该模型不支持调节推理强度, 将不发送 reasoning_effort{C.RESET}")
        else:
            print(f"当前模型: {agent.llm.model}")
    elif line == "/effort" or line.startswith("/effort "):
        efforts = supported_efforts(agent.llm.model)
        effort = line[7:].strip().lower()
        if not efforts:
            print(f"当前模型 {agent.llm.model} 不支持调节推理强度")
        elif effort:
            if effort not in efforts:
                print(f"无效值: {effort} ({agent.llm.model} 可选: {', '.join(efforts)})")
            else:
                agent.llm.effort = effort
                print(f"{C.GREEN}推理强度已切换: {effort}{C.RESET}")
        else:
            print(f"当前推理强度: {agent.llm.effective_effort()} ({agent.llm.model} 可选: {', '.join(efforts)})")
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
    elif line == "/compact":
        try:
            if agent.compact():
                print(f"{C.GREEN}历史已压缩, 当前 {len(agent.messages)} 条消息{C.RESET}")
            else:
                print("对话不足两轮, 无可压缩")
        except LLMError as e:
            print(f"{C.YELLOW}压缩失败: {e}{C.RESET}")
    elif line == "/sessions":
        sessions = list_sessions()
        if not sessions:
            print("没有保存的会话")
        for s in sessions:
            print(f"  {C.GREEN}{s['id']}{C.RESET}  {C.DIM}{s['cwd']}{C.RESET}  {s['preview']}")
    elif line == "/resume" or line.startswith("/resume "):
        sid = line[8:].strip()
        sessions = list_sessions()
        if not sid:
            if not sessions:
                print("没有保存的会话")
                return True
            sid = sessions[0]["id"]
        try:
            load_session(agent, sid)
            print(f"{C.GREEN}已恢复会话 {sid} ({len(agent.messages)} 条消息, cwd: {agent.cwd}){C.RESET}")
        except (OSError, ValueError) as e:
            print(f"恢复失败: {e}")
    elif line == "/reasoning":
        agent.ui.show_reasoning = not agent.ui.show_reasoning
        print(f"思维链显示: {'展开' if agent.ui.show_reasoning else '折叠'}")
    elif line == "/verbose":
        agent.ui.verbose = not agent.ui.verbose
        print(f"工具结果显示: {'默认展开' if agent.ui.verbose else '默认折叠'}")
    elif line == "/output" or line.startswith("/output "):
        if not agent.tool_log:
            print("还没有工具调用记录")
        else:
            arg = line[8:].strip()
            n = int(arg) if arg.isdigit() and int(arg) >= 1 else 1
            if n > len(agent.tool_log):
                print(f"只有 {len(agent.tool_log)} 条记录")
            else:
                name, summary, result = agent.tool_log[-n]
                print(f"{C.GREEN}●{C.RESET} {C.BOLD}{name}{C.RESET} {C.DIM}{summary}{C.RESET}")
                print(result)
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
    effort = agent.llm.effective_effort() or "不支持"
    banner = f"{C.BOLD_CYAN}supa-agent{C.RESET}  ·  {C.GREEN}model: {agent.llm.model}{C.RESET}  ·  {C.YELLOW}effort: {effort}{C.RESET}  ·  {C.CYAN}cwd: {agent.cwd}{C.RESET}\n{C.DIM}输入 / 查看可用命令{C.RESET}"
    if TUI_AVAILABLE:
        agent.ui = TranscriptUI()
        run_app(agent, handle_command, banner=banner)
        return
    print(banner)
    while True:
        try:
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
    ap.add_argument("--yolo", action="store_true", help="跳过所有权限确认 (危险, 适合沙箱/CI)")
    ap.add_argument("--resume", nargs="?", const="latest", default=None, metavar="ID",
                    help="恢复会话 (不带 ID 则恢复最近一个)")
    args = ap.parse_args()

    try:
        llm = LLM(base_url=args.base_url, api_key=args.api_key, model=args.model)
        if args.effort:
            llm.effort = args.effort.lower()
    except LLMError as e:
        print(f"错误: {e}", file=sys.stderr)
        print("需要设置环境变量 LLM_API_KEY, 可选 LLM_BASE_URL / LLM_MODEL, 详见 README", file=sys.stderr)
        sys.exit(1)

    agent = Agent(llm, cwd=str(Path(args.dir).resolve()), policy=Policy(yolo=args.yolo))
    if args.resume:
        sessions = list_sessions()
        sid = sessions[0]["id"] if args.resume == "latest" and sessions else args.resume
        if sid == "latest":
            print("没有保存的会话", file=sys.stderr)
            sys.exit(1)
        try:
            load_session(agent, sid)
            print(f"已恢复会话 {sid} ({len(agent.messages)} 条消息)")
        except (OSError, ValueError) as e:
            print(f"恢复失败: {e}", file=sys.stderr)
            sys.exit(1)
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
