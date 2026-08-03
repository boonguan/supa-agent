"""最小自检: python3 tests/test_harness.py 无输出报错即通过。零依赖, 不需要 API key。"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from harness import session as session_mod
from harness.agent import Agent
from harness.policy import Policy

# 测试期间会话落盘到临时目录, 不污染 ~/.supa
_session_tmp = tempfile.TemporaryDirectory()
session_mod.SESSIONS_DIR = Path(_session_tmp.name)
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

    def context_window(self):
        return 128_000

    chat_response = "摘要: 用户在改文件, 已改 a.txt"  # compact / 自动审核共用, 测试可改

    def chat(self, messages, tools=None):
        return {"choices": [{"message": {"content": self.chat_response}}]}

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
    agent = Agent(llm, cwd=tmp, policy=Policy(yolo=True))
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


def test_policy(tmp):
    # 只读放行 / bash 白名单 / 修改类询问 / yolo / always 记忆
    p = Policy()
    assert p.check("read_file", {}, True) == "allow"
    assert p.check("bash", {"command": "ls -la"}, False) == "allow"
    assert p.check("bash", {"command": "git status"}, False) == "allow"
    assert p.check("bash", {"command": "ls && rm -rf /"}, False) == "ask"  # 元字符不放行
    assert p.check("bash", {"command": "rm -rf build"}, False) == "ask"
    assert p.check("edit_file", {}, False) == "ask"
    p.remember("bash", {"command": "rm -rf build"})
    assert p.check("bash", {"command": "rm foo.txt"}, False) == "allow"  # 记住 bash:rm
    assert p.check("bash", {"command": "curl x"}, False) == "ask"
    assert Policy(yolo=True).check("bash", {"command": "rm -rf /"}, False) == "allow"

    # 拒绝路径: confirm 返回 n -> 工具不执行, 回复拒绝消息
    import contextlib
    import io

    target = Path(tmp) / "c.txt"
    target.write_text("orig", encoding="utf-8")
    llm = FakeLLM([
        ("", [("write_file", {"path": "c.txt", "content": "hacked"})]),
        ("好的", None),
    ])
    agent = Agent(llm, cwd=tmp)
    agent.ui.confirm = lambda name, summary, depth: "n"
    with contextlib.redirect_stdout(io.StringIO()):
        agent.chat("改文件")
    assert target.read_text() == "orig"  # 未被执行
    assert any(m["role"] == "tool" and "拒绝" in m["content"] for m in agent.messages)


def test_auto_review(tmp):
    import contextlib
    import io

    # 模型判 ALLOW: 不询问直接执行
    llm = FakeLLM([("", [("write_file", {"path": "d.txt", "content": "ok"})]), ("完成", None)])
    llm.chat_response = "ALLOW\n常规项目内写文件"
    agent = Agent(llm, cwd=tmp, policy=Policy(auto=True))
    asked = []
    agent.ui.confirm = lambda *a: asked.append(1) or "n"
    with contextlib.redirect_stdout(io.StringIO()):
        agent.chat("写文件")
    assert (Path(tmp) / "d.txt").read_text() == "ok" and not asked
    assert agent.llm.effort == "medium"  # 审核后 effort 恢复原值

    # 模型判 DENY: 升级人工, 人工拒绝 -> 不执行
    llm2 = FakeLLM([("", [("write_file", {"path": "e.txt", "content": "bad"})]), ("好", None)])
    llm2.chat_response = "DENY\n可疑操作"
    agent2 = Agent(llm2, cwd=tmp, policy=Policy(auto=True))
    agent2.ui.confirm = lambda *a: "n"
    with contextlib.redirect_stdout(io.StringIO()):
        agent2.chat("写文件")
    assert not (Path(tmp) / "e.txt").exists()

    # 确认时选 auto: 开启自动审核并立即审批当前操作
    llm3 = FakeLLM([("", [("write_file", {"path": "f.txt", "content": "ok"})]), ("好", None)])
    llm3.chat_response = "ALLOW\n安全"
    agent3 = Agent(llm3, cwd=tmp)
    agent3.ui.confirm = lambda *a: "auto"
    with contextlib.redirect_stdout(io.StringIO()):
        agent3.chat("写文件")
    assert agent3.policy.auto is True
    assert (Path(tmp) / "f.txt").read_text() == "ok"


def test_interrupt_cleanup(tmp):
    agent = Agent(FakeLLM([]), cwd=tmp)
    agent.messages += [
        {"role": "user", "content": "x"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "bash", "arguments": "{}"}},
            {"id": "c2", "type": "function", "function": {"name": "bash", "arguments": "{}"}},
        ]},
        {"role": "tool", "tool_call_id": "c1", "content": "done"},
    ]
    agent._interrupt_cleanup()  # c2 缺回复, 应补齐
    last = agent.messages[-1]
    assert last["role"] == "tool" and last["tool_call_id"] == "c2" and "中断" in last["content"]
    agent._interrupt_cleanup()  # 幂等: 不重复补
    assert sum(1 for m in agent.messages if m["role"] == "tool") == 2


def test_llm_retry(monkeypatched=None):
    import io as _io
    import urllib.error
    import urllib.request

    from harness import llm as llm_mod
    from harness.llm import LLM

    llm = LLM.__new__(LLM)
    llm.base_url, llm.api_key, llm.model, llm.effort = "http://x", "k", "m", "high"
    llm._resp, llm._aborted = None, False

    calls = []
    real_sleep = llm_mod.time.sleep
    llm_mod.time.sleep = lambda s: None  # 免等
    try:
        def fake_urlopen(req, timeout=None):
            calls.append(1)
            if len(calls) < 3:
                raise urllib.error.HTTPError("http://x", 429, "too many", {}, _io.BytesIO(b""))
            return "OK"

        real_urlopen = urllib.request.urlopen
        urllib.request.urlopen = fake_urlopen
        try:
            assert llm._post({"a": 1}) == "OK"  # 429 两次后第三次成功
            assert len(calls) == 3
        finally:
            urllib.request.urlopen = real_urlopen
    finally:
        llm_mod.time.sleep = real_sleep


def test_env_context(tmp):
    import subprocess

    prompt = build_system_prompt("m1", tmp)
    assert "平台" in prompt and "日期" in prompt and "不是 git 仓库" in prompt
    subprocess.run(["git", "init", "-q", "-b", "main", tmp], check=True)
    prompt = build_system_prompt("m1", tmp)
    assert "分支 main" in prompt


def test_parallel_readonly_tools(tmp):
    import contextlib
    import io

    (Path(tmp) / "f1.txt").write_text("alpha", encoding="utf-8")
    (Path(tmp) / "f2.txt").write_text("beta", encoding="utf-8")
    llm = FakeLLM([
        ("", [("read_file", {"path": "f1.txt"}), ("read_file", {"path": "f2.txt"}), ("list_dir", {})]),
        ("看完了", None),
    ])
    agent = Agent(llm, cwd=tmp)
    with contextlib.redirect_stdout(io.StringIO()):
        assert agent.chat("看两个文件") == "看完了"
    tools_msgs = [m for m in agent.messages if m["role"] == "tool"]
    assert len(tools_msgs) == 3
    # 结果按原顺序回填, tool_call_id 对应
    assert "alpha" in tools_msgs[0]["content"] and tools_msgs[0]["tool_call_id"] == "call_0"
    assert "beta" in tools_msgs[1]["content"] and tools_msgs[1]["tool_call_id"] == "call_1"
    assert "f1.txt" in tools_msgs[2]["content"]


def test_grep_and_read_offset(tmp):
    from harness.tools import grep, read_file

    agent = Agent(FakeLLM([]), cwd=tmp)
    (Path(tmp) / "code.py").write_text("aaa\nneedle here\nbbb\n", encoding="utf-8")
    out = grep(agent, "needle")
    assert "code.py" in out and "needle here" in out
    assert grep(agent, "不存在的串") == "(无匹配)"

    (Path(tmp) / "big.txt").write_text("\n".join(f"L{i}" for i in range(1, 101)), encoding="utf-8")
    out = read_file(agent, "big.txt", offset=50, limit=10)
    assert out.startswith("50: L50") and "59: L59" in out and "offset=60" in out
    assert "超出范围" in read_file(agent, "big.txt", offset=999)


def test_usage_and_compact(tmp):
    import contextlib
    import io

    llm = FakeLLM([("第一轮回复", None), ("第二轮回复", None), ("第三轮回复", None)])
    agent = Agent(llm, cwd=tmp)
    with contextlib.redirect_stdout(io.StringIO()):
        agent.chat("任务一")
        agent.chat("任务二")
        agent.chat("任务三")
    # usage 累计 (FakeLLM 每轮 usage 只有 reasoning_tokens=7)
    assert agent.total_usage["requests"] == 3
    assert agent.total_usage["reasoning_tokens"] == 21
    # compact: 前两轮被摘要替换, 当前轮保留
    n_before = len(agent.messages)
    assert agent.compact() is True
    assert any("(历史摘要)" in (m.get("content") or "") for m in agent.messages)
    assert agent.messages[-1]["content"] == "第三轮回复"  # 当前轮完整保留
    assert len(agent.messages) < n_before
    # 只剩一轮时不可再压
    assert agent.compact() is False

    # 旧工具输出裁剪
    agent2 = Agent(FakeLLM([]), cwd=tmp)
    agent2.messages += [{"role": "user", "content": "x"}]
    agent2.messages += [{"role": "tool", "tool_call_id": f"c{i}", "content": "y" * 1000} for i in range(20)]
    agent2._prune_old_tool_results()
    old, recent = agent2.messages[2], agent2.messages[-1]
    assert old["content"].endswith("(旧工具输出已裁剪)") and len(old["content"]) < 300
    assert len(recent["content"]) == 1000  # 最近的不动


def test_session_persistence(tmp):
    import contextlib
    import io

    llm = FakeLLM([("好", None)])
    agent = Agent(llm, cwd=tmp)
    agent.todos = [{"content": "todo1", "status": "pending"}]
    with contextlib.redirect_stdout(io.StringIO()):
        agent.chat("记住这个")  # chat 结束自动落盘
    sessions = session_mod.list_sessions()
    assert sessions and sessions[0]["preview"].startswith("记住这个")

    agent2 = Agent(FakeLLM([]), cwd="/")
    session_mod.load(agent2, agent.session_id)
    assert agent2.messages == agent.messages
    assert agent2.todos[0]["content"] == "todo1"
    assert agent2.cwd == tmp  # cwd 一并恢复


def test_agents_md_and_custom_agents(tmp):
    from harness.context import discover_agents, memory_path

    # 无记忆文件时 remember 新建 AGENTS.md; 已有 SUPA.md 则继续写 SUPA.md
    assert memory_path(tmp).name == "AGENTS.md"
    (Path(tmp) / "SUPA.md").write_text("- 旧记忆\n", encoding="utf-8")
    assert memory_path(tmp).name == "SUPA.md"
    (Path(tmp) / "AGENTS.md").write_text("# 规范\n- 用中文\n", encoding="utf-8")
    assert memory_path(tmp).name == "AGENTS.md"  # 两者都有时优先 AGENTS.md
    memory = load_memory(tmp)
    assert "用中文" in memory and "旧记忆" in memory

    # 自定义子代理类型
    agents_dir = Path(tmp) / ".supa" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "reviewer.md").write_text("---\nname: reviewer\ndescription: 代码审查\n---\n只报缺陷, 不夸代码。", encoding="utf-8")
    agents = discover_agents(tmp)
    assert agents[0]["name"] == "reviewer" and "只报缺陷" in agents[0]["prompt"]
    assert "reviewer" in build_system_prompt("m", tmp)

    # task 用类型化子代理: 子代理 system prompt 带角色指令
    import contextlib
    import io

    from harness.tools import task as task_tool

    llm = FakeLLM([("审查完成", None)])
    agent = Agent(llm, cwd=tmp)
    with contextlib.redirect_stdout(io.StringIO()):
        result = task_tool(agent, "审查", "审查这段代码", agent_type="reviewer")
    assert result == "审查完成"
    assert "未定义的子代理类型" in task_tool(agent, "x", "y", agent_type="不存在")


def test_bash_background_and_jobs(tmp):
    import time

    from harness.tools import job_output, run_bash

    agent = Agent(FakeLLM([]), cwd=tmp)
    out = run_bash(agent, "echo hello-bg; sleep 0.1", background=True)
    assert "后台任务 #1" in out
    for _ in range(50):
        if agent.jobs[0]["proc"].poll() is not None:
            break
        time.sleep(0.05)
    report = job_output(agent, 1)
    assert "hello-bg" in report and "已结束" in report
    assert "没有后台任务" in job_output(agent, 99)
    # 超时参数
    assert "超时 (1s)" in run_bash(agent, "sleep 5", timeout=1)


def test_config(tmp):
    from harness.config import load_config

    proj = Path(tmp) / ".supa"
    proj.mkdir(parents=True)
    (proj / "config.json").write_text(json.dumps({"model": "deepseek-v4-pro", "bash_allow": ["docker ps"]}), encoding="utf-8")
    cfg = load_config(tmp)
    assert cfg["model"] == "deepseek-v4-pro"
    p = Policy(extra_prefixes=cfg["bash_allow"])
    assert p.check("bash", {"command": "docker ps -a"}, False) == "allow"
    assert p.check("bash", {"command": "docker rm x"}, False) == "ask"


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
    import io

    try:
        from prompt_toolkit.input import create_pipe_input

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

    # tab 展开为空格 (否则渲染成 ^I); 点击展开走 on_redraw 而非 on_change (不跳底部)
    ui2 = TranscriptUI()
    ui2.tool_call("bash", "cat", 0)
    ui2.tool_result("bash", "a\tb", 0)
    ui2.blocks[-1]["expanded"] = True
    assert "\t" not in "".join(f[1] for f in ui2.fragments())
    ui2.on_content("有\t制表符\n", 0)
    ui2.end_content(0)
    assert "\t" not in ui2.blocks[-1]["ansi"]

    # 视口窗口化: 巨大展开块只渲染锚点附近 VIEW_LINES 行 (整屏渲染会卡死)
    ui3 = TranscriptUI()
    ui3.tool_call("bash", "big", 0)
    ui3.tool_result("bash", "\n".join(f"line {i}" for i in range(8000)), 0)
    ui3.blocks[-1]["expanded"] = True
    frags = ui3.fragments()
    assert sum(f[1].count("\n") for f in frags) <= ui3.VIEW_LINES + 2
    tail = "".join(f[1] for f in frags)
    assert "line 7999" in tail and "line 100\n" not in tail  # 跟随底部: 只见末尾
    ui3.follow, ui3.anchor = False, 4000
    mid = "".join(f[1] for f in ui3.fragments())
    assert "line 4000" in mid and "line 7999" not in mid  # 锚定中部: 只见附近

    from prompt_toolkit.mouse_events import MouseEvent, MouseEventType
    from prompt_toolkit.data_structures import Point

    calls = {"change": 0, "redraw": 0}
    ui2.on_change = lambda: calls.__setitem__("change", calls["change"] + 1)
    ui2.on_redraw = lambda: calls.__setitem__("redraw", calls["redraw"] + 1)
    handler = ui2._toggle(ui2.blocks[0])
    handler(MouseEvent(position=Point(0, 0), event_type=MouseEventType.MOUSE_UP,
                       button=None, modifiers=frozenset()))
    assert calls == {"change": 0, "redraw": 1}

    # 全屏 app 真实渲染: 内容必须出现在屏幕上 (回归: FollowPane 滚动钳制)
    import re
    import tempfile
    import threading
    import time

    from prompt_toolkit.data_structures import Size
    from prompt_toolkit.output.vt100 import Vt100_Output

    import main as main_mod

    out_buf = io.StringIO()
    vt = Vt100_Output(out_buf, lambda: Size(rows=30, columns=80))
    with tempfile.TemporaryDirectory() as tmp:
        llm = FakeLLM([("", [("bash", {"command": "echo hi"})]), ("**完成**了", None)])
        agent = Agent(llm, cwd=tmp, ui=TranscriptUI(), policy=Policy(yolo=True))
        with create_pipe_input() as pipe:
            pipe.send_text("跑个命令\n")
            threading.Thread(target=lambda: (time.sleep(1.0), pipe.send_text("/exit\n")), daemon=True).start()
            run_app(agent, main_mod.handle_command, banner="supa-banner", input=pipe, output=vt)
    screen = re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b[>=]", "", out_buf.getvalue())
    for s in ("supa-banner", "跑个命令", "bash", "完成", "已思考"):
        assert s in screen, f"屏幕上看不到: {s}"

    # 权限确认流: write_file 触发确认块, 按 y 放行后执行
    out_buf2 = io.StringIO()
    vt2 = Vt100_Output(out_buf2, lambda: Size(rows=30, columns=80))
    with tempfile.TemporaryDirectory() as tmp:
        llm = FakeLLM([("", [("write_file", {"path": "x.txt", "content": "data"})]), ("写好了", None)])
        agent = Agent(llm, cwd=tmp, ui=TranscriptUI())
        with create_pipe_input() as pipe:
            pipe.send_text("写个文件\n")

            def later():
                time.sleep(0.8)
                pipe.send_text("1")  # 菜单数字直选: 1=允许
                time.sleep(0.8)
                pipe.send_text("/exit\n")

            threading.Thread(target=later, daemon=True).start()
            run_app(agent, main_mod.handle_command, input=pipe, output=vt2)
        assert (Path(tmp) / "x.txt").read_text() == "data"
    screen2 = re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b[>=]", "", out_buf2.getvalue())
    assert "write_file" in screen2 and "1. 允许" in screen2 and "已允许" in screen2 and "写好了" in screen2


def main():
    test_tool_registry()
    test_markdown_render()
    test_effort_per_model()
    test_llm_retry()
    test_tui()
    for fn in (test_memory_and_skills, test_agent_loop_with_tools, test_edit_file_guards, test_subagent,
               test_reasoning_collapse, test_policy, test_auto_review, test_interrupt_cleanup, test_env_context,
               test_usage_and_compact, test_session_persistence,
               test_parallel_readonly_tools, test_grep_and_read_offset,
               test_agents_md_and_custom_agents, test_bash_background_and_jobs, test_config):
        with tempfile.TemporaryDirectory() as tmp:
            fn(tmp)
    print("所有测试通过")


if __name__ == "__main__":
    main()
