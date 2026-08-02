import json

from .llm import LLM
from .tools import TOOLS

SYSTEM_PROMPT = """你是一个运行在用户 shell 里的 coding agent。
你可以调用工具来查看、修改文件和执行命令。规则:
1. 先探索再动手, 用 list_dir / read_file 了解现状。
2. 用 bash 运行测试或验证你的修改。
3. 完成后用自然语言总结你做了什么。"""


class Agent:
    def __init__(self, llm, cwd, max_steps=30):
        self.llm = llm
        self.cwd = cwd
        self.max_steps = max_steps

    def run(self, task):
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": task},
        ]
        schemas = [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in TOOLS
        ]
        for step in range(1, self.max_steps + 1):
            print(f"\n--- step {step} ---")
            resp = self.llm.chat(messages, tools=schemas)
            choice = resp["choices"][0]["message"]
            tool_calls = choice.get("tool_calls")
            if not tool_calls:
                return choice.get("content", "")
            messages.append(choice)
            for call in tool_calls:
                fn_name = call["function"]["name"]
                fn = next(t for t in TOOLS if t.name == fn_name)
                try:
                    args = json.loads(call["function"]["arguments"] or "{}")
                except json.JSONDecodeError:
                    args = {}
                print(f"  {fn_name}({json.dumps(args, ensure_ascii=False)[:200]})")
                try:
                    result = fn(self.cwd, **args)
                except Exception as e:
                    result = f"工具出错: {type(e).__name__}: {e}"
                messages.append(
                    {"role": "tool", "tool_call_id": call["id"], "content": result}
                )
        return "已达到最大步数, 任务未完成"
