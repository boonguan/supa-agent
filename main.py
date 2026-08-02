import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from harness.agent import Agent
from harness.llm import LLM, LLMError


def main():
    ap = argparse.ArgumentParser(description="zero-dependency coding agent harness")
    ap.add_argument("task", nargs="+", help="任务描述")
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
    print(f"工作目录: {agent.cwd}")
    print(agent.run(" ".join(args.task)))


if __name__ == "__main__":
    main()
