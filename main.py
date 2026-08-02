import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from harness.agent import Agent
from harness.llm import LLM, LLMError

HELP = """命令:
  /exit             退出
  /reset            清空对话历史
  /cwd <路径>       切换工作目录
  /help             显示帮助
其余输入都会作为任务发给 agent"""


def repl(agent):
    print(f"supa-agent 交互模式, 当前目录: {agent.cwd}")
    print(HELP)
    while True:
        try:
            line = input(f"{agent.cwd} > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nbye")
            break
        if not line:
            continue
        if line in ("/exit", "/quit", "/q"):
            break
        if line == "/help":
            print(HELP)
        elif line == "/reset":
            agent.reset()
            print("历史已清空")
        elif line.startswith("/cwd "):
            p = Path(line[5:].strip()).expanduser()
            if p.exists() and p.is_dir():
                agent.cwd = str(p.resolve())
                print(f"已切换: {agent.cwd}")
            else:
                print(f"目录不存在: {p}")
        else:
            try:
                agent.chat(line)
            except KeyboardInterrupt:
                print("\n(已中断)")


def main():
    ap = argparse.ArgumentParser(description="zero-dependency coding agent harness")
    ap.add_argument("task", nargs="*", help="任务描述 (省略则进入交互模式)")
    ap.add_argument("-d", "--dir", default=".", help="工作目录 (默认当前目录)")
    ap.add_argument("--base-url", default=None, help="API base url, 默认取 LLM_BASE_URL")
    ap.add_argument("--api-key", default=None, help="API key, 默认取 LLM_API_KEY")
    ap.add_argument("--model", default=None, help="模型名, 默认取 LLM_MODEL")
    args = ap.parse_args()

    try:
        llm = LLM(base_url=args.base_url, api_key=args.api_key, model=args.model)
    except LLMError as e:
        print(f"错误: {e}", file=sys.stderr)
        print("需要设置环境变量 LLM_API_KEY, 可选 LLM_BASE_URL / LLM_MODEL, 详见 README", file=sys.stderr)
        sys.exit(1)

    agent = Agent(llm, cwd=str(Path(args.dir).resolve()))
    if args.task:
        agent.run(" ".join(args.task))
    else:
        repl(agent)


if __name__ == "__main__":
    main()
