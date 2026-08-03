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

    def effective_effort(self):
        return self.effort

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
        # include_usage 的末尾 chunk: 无 choices, 只有 usage
        yield {"choices": [], "usage": {"completion_tokens_details": {"reasoning_tokens": 7}}}


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
    # 工具日志供 /output 回看
    assert [t[0] for t in agent.tool_log] == ["todo_write", "edit_file"]
    assert "已修改" in agent.tool_log[-1][2]


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


def test_markdown_render():
    import contextlib
    import io

    from harness.ui import render_markdown

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        render_markdown("## 标题\n**加粗** 和 `代码`\n- 列表项\n| 名字 | 值 |\n|---|---|\n| 甲 | 1 |\n```\ncode here\n```")
    out = buf.getvalue()
    assert "##" not in out and "**" not in out and "|---|" not in out  # 标记被消化
    assert "标题" in out and "\033[1m加粗" in out and "• 列表项" in out
    assert "甲" in out and "code here" in out


def test_reasoning_collapse(tmp):
    import contextlib
    import io

    llm = FakeLLM([("你好", None)])
    agent = Agent(llm, cwd=tmp)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        agent.chat("hi")
    out = buf.getvalue()
    assert "已思考" in out and "tokens" in out and "想一想" not in out  # 默认折叠, 显示 token 数

    llm2 = FakeLLM([("你好", None)])
    agent2 = Agent(llm2, cwd=tmp)
    agent2.ui.show_reasoning = True
    buf2 = io.StringIO()
    with contextlib.redirect_stdout(buf2):
        agent2.chat("hi")
    assert "想一想" in buf2.getvalue()  # 展开时输出原文


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
    assert llm._payload([], stream=True) == {
        "model": "deepseek-v4-pro",
        "messages": [],
        "stream": True,
        "stream_options": {"include_usage": True},
        "reasoning_effort": "max",
    }


def test_tui():
    try:
        from prompt_toolkit.input import create_pipe_input
        from prompt_toolkit.output import DummyOutput

        from harness.tui import TranscriptUI, run_app
    except ImportError:
        return  # 无 prompt_toolkit 时跳过

    # TranscriptUI: 块结构与点击展开
    ui = TranscriptUI()
    ui.on_content("## 标题\n正文 **加粗**\n", 0)
    ui.end_content(0)
    ui.tool_call("bash", "git log", 0)
    ui.tool_result("bash", "line1\nline2\nline3", 0)
    ui.on_reasoning("想", 1, 0)
    ui.end_reasoning("7 tokens", "想", 0)
    kinds = [b["kind"] for b in ui.blocks]
    assert kinds == ["text", "tool", "reasoning"]
    text = "".join(f[1] for f in ui.fragments())
    assert "标题" in text and "##" not in text  # markdown 已渲染
    assert "+2 行" in text and "line2" not in text  # 工具块默认折叠
    tool_block = ui.blocks[1]
    tool_block["expanded"] = True  # 模拟点击展开
    text = "".join(f[1] for f in ui.fragments())
    assert "line2" in text and "line3" in text
    assert "已思考 (7 tokens)" in text

    # 全屏 app: 发 /exit 能干净退出
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        import main as main_mod

        agent = Agent(FakeLLM([]), cwd=tmp, ui=TranscriptUI())
        with create_pipe_input() as pipe:
            pipe.send_text("/help\n/exit\n")
            run_app(agent, main_mod.handle_command, banner="hi", input=pipe, output=DummyOutput())
        assert any(b["kind"] == "user" and b["text"] == "/help" for b in agent.ui.blocks)


def main():
    test_tool_registry()
    test_markdown_render()
    test_effort_per_model()
    test_tui()
    for fn in (test_memory_and_skills, test_agent_loop_with_tools, test_edit_file_guards, test_subagent, test_reasoning_collapse):
        with tempfile.TemporaryDirectory() as tmp:
            fn(tmp)
    print("所有测试通过")


if __name__ == "__main__":
    main()
