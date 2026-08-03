# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目

supa-agent：轻量 coding agent CLI。核心零依赖（纯 Python 标准库），TUI 仅需 prompt_toolkit。对接任何 OpenAI 兼容 API（默认 DeepSeek）。代码注释与文档均为中文。

## 常用命令

```bash
python3 tests/test_harness.py      # 全部自检：零依赖、不需 API key，无输出报错即通过
pip install -e .                   # 安装后可用 `supa` 命令
supa "任务描述" -d /path           # 一次性任务模式
supa -d /path                      # 交互 TUI 模式（需 LLM_API_KEY 环境变量）
```

没有 lint / 单测框架；tests/test_harness.py 是唯一测试入口（assert 风格，用 FakeLLM 回放脚本化流式响应，不打真实 API）。

## 架构

调用链：`main.py`（argparse 入口，交互/一次性两种模式）→ `harness/agent.py`（Agent 主循环：流式响应、工具调用分发、上下文裁剪/压缩、子代理深度限 1 层）→ `harness/llm.py`（OpenAI 兼容客户端，urllib 实现，流式 + 非流式，按模型映射 reasoning effort）。

- **harness/tools.py** — 工具注册中心。加新工具只需 `@tool(name, desc, json_schema)` 装饰一个函数，第一个参数是 agent 实例（可取 `agent.cwd` / `agent.llm` / `agent.todos`），schema 自动进模型上下文，无需其他改动。
- **harness/policy.py** — 权限策略：bash 命令前缀白名单 + 危险操作确认；`--yolo` 跳过。
- **harness/context.py** — 项目记忆（`SUPA.md`/`AGENTS.md` 自动载入系统提示）与 skills 发现（`.supa/skills/*/SKILL.md`）。
- **harness/session.py** — 会话持久化到 `~/.supa/sessions/`（`--resume` 恢复）。测试通过替换模块级 `SESSIONS_DIR` 重定向到临时目录。
- **harness/tui.py** / **ui.py** — tui 是 prompt_toolkit 全屏界面（输入框/状态栏/斜杠补全），ui 是终端渲染（工具折叠行/彩色 diff/todo 清单）。

配置优先级：CLI 参数 > 环境变量（`LLM_API_KEY`/`LLM_BASE_URL`/`LLM_MODEL`/`LLM_EFFORT`）> 项目 `.supa/config.json` > 全局 `~/.supa/config.json`（见 harness/config.py）。

同一轮的多个只读工具调用会自动线程池并发；bash 支持 `background=true` 后台任务。
