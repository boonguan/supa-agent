"""最小自检: python3 tests/test_harness.py 无输出报错即通过。零依赖, 不需要 API key。"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from harness.agent import Agent
from harness.context import append_memory, build_system_prompt, discover_skills, load_memory
from harness.tools import TOOLS


class FakeLLM:
    """按脚本回放流式响应, 每次 chat_stream 消耗一轮。"""

    def __init__(self, rounds):
        self.model = "fake-model"
        self.effort = "medium"
        self.rounds = list(rounds)
        self.last_tools = None

    def chat_stream(self, messages, tools=None):
        self.last_tools = tools
        content, tool_calls = self.rounds.pop(0)
        yield {"choices": [{"delta": {"reasoning_content": "想一想"}}]}  # 模拟思维链
        if content:
            yield {"choices": [{"delta": {"content": content}}]}
        if tool_calls:
            deltas = []
            for i, (name, args) in enumerate(tool_calls):
                deltas.append(
                    {
                        "index": i,
                        "id": f"call_{i}",
                        "function": {"name": name, "arguments": json.dumps(args)},
                    }
                )
            yield {"choices": [{"delta": {"tool_calls": deltas}}]}


def test_tool_registry():
    names = {t.name for t in TOOLS}
    assert {"bash", "read_file", "write_file", "edit_file", "list_dir", "grep",
            "todo_write", "task", "remember"} <= names


def test_memory_and_skills(tmp):
    assert load_memory(tmp) == ""
    append_memory(tmp, "用户喜欢中文注释")
    assert "用户喜欢中文注释" in load_memory(tmp)

    skill_dir = Path(tmp) / ".supa" / "skills" / "deploy"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: deploy\ndescription: 部署流程\n---\n步骤...", encoding="utf-8")
    skills = discover_skills(tmp)
    assert skills[0]["name"] == "deploy" and skills[0]["description"] == "部署流程"

    prompt = build_system_prompt("m1", tmp)
    assert "用户喜欢中文注释" in prompt and "deploy" in prompt and "m1" in prompt


def test_agent_loop_with_tools(tmp):
    target = Path(tmp) / "a.txt"
    target.write_text("hello world\n", encoding="utf-8")
    llm = FakeLLM([
        ("", [("todo_write", {"todos": [{"content": "改文件", "status": "in_progress"}]}),
              ("edit_file", {"path": "a.txt", "old": "hello", "new": "hi"})]),
        ("完成", None),
    ])
    agent = Agent(llm, cwd=tmp)
    result = agent.chat("把 hello 改成 hi")
    assert result == "完成"
    assert target.read_text() == "hi world\n"
    assert agent.todos[0]["content"] == "改文件"
    # 消息序列: 最终 assistant 回复也入历史 (多轮对话连续性)
    assert [m["role"] for m in agent.messages] == ["system", "user", "assistant", "tool", "tool", "assistant"]
    # DeepSeek 思考模式: assistant 消息必须回传 reasoning_content
    assert all(m["reasoning_content"] == "想一想" for m in agent.messages if m["role"] == "assistant")
    assert agent.messages[-1]["content"] == "完成"


def test_edit_file_guards(tmp):
    from harness.tools import edit_file

    p = Path(tmp) / "b.txt"
    p.write_text("x x", encoding="utf-8")
    agent = Agent(FakeLLM([]), cwd=tmp)
    assert "不唯一" in edit_file(agent, "b.txt", "x", "y")
    assert "未找到" in edit_file(agent, "b.txt", "zzz", "y")
    assert "文件不存在" in edit_file(agent, "nope.txt", "a", "b")
    assert p.read_text() == "x x"


def test_subagent(tmp):
    # 父代理调 task -> 子代理跑一轮直接返回文本
    llm = FakeLLM([
        ("", [("task", {"description": "调研", "prompt": "看看目录"})]),  # 父第 1 轮
        ("子代理结论: 目录为空", None),                                    # 子代理的轮
        ("汇总完毕", None),                                               # 父第 2 轮
    ])
    agent = Agent(llm, cwd=tmp)
    assert agent.chat("调研一下") == "汇总完毕"
    tool_msg = next(m for m in agent.messages if m["role"] == "tool")
    assert "子代理结论" in tool_msg["content"]
    # 子代理的工具表不含 task
    sub = Agent(llm, cwd=tmp, depth=1)
    assert all(t.name != "task" for t in sub.tools)


def test_effort_per_model():
    from harness.llm import LLM, supported_efforts

    assert supported_efforts("deepseek-v4-flash") == ("none", "low", "high", "max")
    assert supported_efforts("gpt-5") == ("low", "medium", "high")
    assert supported_efforts("qwen2.5-coder") == ()

    llm = LLM.__new__(LLM)  # 跳过 __init__ 的 api_key 检查
    llm.model, llm.effort = "deepseek-v4-flash", "medium"
    assert llm.effective_effort() == "high"  # deepseek 无 medium, 就近向上取
    assert llm._payload([])["reasoning_effort"] == "high"
    llm.effort = "none"  # 关闭思考: 发 thinking disabled 而非 reasoning_effort
    p = llm._payload([])
    assert p["thinking"] == {"type": "disabled"} and "reasoning_effort" not in p
    llm.model = "qwen2.5-coder"
    assert llm.effective_effort() is None
    assert "reasoning_effort" not in llm._payload([])  # 不支持的模型不发参数
    llm.model, llm.effort = "deepseek-v4-pro", "max"
    assert llm._payload([], stream=True) == {"model": "deepseek-v4-pro", "messages": [], "stream": True, "reasoning_effort": "max"}


def test_tui():
    try:
        from prompt_toolkit.input import create_pipe_input
        from prompt_toolkit.output import DummyOutput

        from harness.tui import create_session, prompt_line
    except ImportError:
        return  # 无 prompt_toolkit 时跳过

    class FakeAgent:
        cwd = "/tmp"

        class llm:
            model = "deepseek-v4-flash"
            effort = "medium"

            @staticmethod
            def effective_effort():
                return "medium"

    with create_pipe_input() as pipe:
        session = create_session(input=pipe, output=DummyOutput())
        pipe.send_text("hello\n")
        assert prompt_line(session, FakeAgent()) == "hello"
        pipe.send_text("a\x1b\rb\n")  # Alt+Enter 换行, Enter 发送
        assert prompt_line(session, FakeAgent()) == "a\nb"


def main():
    test_tool_registry()
    test_effort_per_model()
    test_tui()
    for fn in (test_memory_and_skills, test_agent_loop_with_tools, test_edit_file_guards, test_subagent):
        with tempfile.TemporaryDirectory() as tmp:
            fn(tmp)
    print("所有测试通过")


if __name__ == "__main__":
    main()
