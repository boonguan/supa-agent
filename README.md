# supa-agent

一个轻量的 coding agent:给模型一套工具 (bash / read_file / write_file / list_dir / grep),让它自主完成编码任务,像 Claude Code 一样在终端里聊着干活。核心 agent 零依赖 (纯 Python 标准库), TUI 输入框仅需 prompt_toolkit。兼容任何 OpenAI-format 的 API (DeepSeek / OpenAI / Ollama / vLLM 等)。

## 功能

- **交互式 TUI** — 真正的输入框:多行输入、历史记录 (上下箭头)、Enter 发送 / Alt+Enter 换行;输入框与会话内容颜色区分 (参考 opencode 风格),斜杠命令自动补全
- **状态栏** — 底部实时显示模型型号、reasoning effort 和工作目录
- **模型切换** — `/model` 随时切换模型, 无需重启
- **推理强度** — `/effort` 切换 reasoning effort (low / medium / high)
- **自主编码** — agent 自动调用 bash / 文件读写 / 搜索工具,无需人工干预
- **斜杠命令** — `/exit`、`/reset`、`/model`、`/effort`、`/cwd`、`/help`
- **轻依赖** — 核心仅用 Python 标准库,TUI 只需 prompt_toolkit
- **API 兼容** — 任何 OpenAI-format 端点都能接 (DeepSeek / OpenAI / Ollama / vLLM)
- **可扩展** — 加一个函数就能注册新工具
- **双模式** — 交互式对话,或一次性执行单个任务

## 快速开始

### 1. 安装

需要 Python 3.8+。安装时自动带上 TUI 依赖:

```bash
git clone git@github.com:boonguan/supa-agent.git
cd supa-agent
python3 -m pip install -e .   # Ubuntu/Debian 需要加 --user --break-system-packages
```

装完直接使用 `supa` 命令 (也可以不安装,用 `python3 main.py` 代替)。

### 2. 配置 API key

```bash
export LLM_API_KEY=sk-xxx                              # 必填
export LLM_BASE_URL=https://api.deepseek.com/v1        # 可选, 默认 DeepSeek
export LLM_MODEL=deepseek-chat                         # 可选
export LLM_EFFORT=medium                               # 可选, 推理强度: low/medium/high
```

也可以复制 `.env.example` 后自行 source:

```bash
cp .env.example .env && vim .env    # 填入 LLM_API_KEY
set -a && source .env && set +a
```

或用命令行参数 `--api-key` / `--base-url` / `--model`,优先级高于环境变量。

### 3. 使用

**交互模式** (推荐, 像 Claude Code 一样连续对话):

```bash
supa -d /path/to/your/project
```

```
supa-agent  ·  model: deepseek-chat  ·  effort: medium  ·  cwd: /path/to/your/project
输入 / 查看可用命令
> 看看这个项目是做什么的
> 给代码加上错误处理
> /exit
```

**一次性任务模式** (适合脚本调用 / CI):

```bash
supa "看看当前目录有什么文件, 然后写一个 hello.py 并运行它" -d /tmp/demo
```

### 4. 斜杠命令

| 命令 | 说明 |
|---|---|
| `/exit` | 退出 |
| `/reset` | 清空对话历史 |
| `/model [名称]` | 查看当前模型, 或切换 (如 `/model deepseek-reasoner`) |
| `/effort [级别]` | 查看或切换推理强度 (low / medium / high) |
| `/cwd <路径>` | 切换工作目录 |
| `/help` | 显示帮助 |

输入 `/` 加 Tab 或直接输入可见自动补全建议。

## 配置项

| 环境变量 | 必填 | 默认值 | 说明 |
|---|---|---|---|
| `LLM_API_KEY` | 是 | - | API key |
| `LLM_BASE_URL` | 否 | `https://api.deepseek.com/v1` | OpenAI 兼容 base url |
| `LLM_MODEL` | 否 | `deepseek-chat` | 模型名 |
| `LLM_EFFORT` | 否 | `medium` | 推理强度 (low / medium / high), 作为 `reasoning_effort` 传给 API |

其他兼容端点示例:

```bash
# OpenAI
export LLM_BASE_URL=https://api.openai.com/v1 LLM_MODEL=gpt-4o-mini
# Ollama (本地, 免费)
export LLM_BASE_URL=http://localhost:11434/v1 LLM_MODEL=qwen2.5-coder
# DeepSeek 推理模型
export LLM_BASE_URL=https://api.deepseek.com/v1 LLM_MODEL=deepseek-reasoner
```

## 内置工具

| 工具 | 说明 |
|---|---|
| `bash` | 执行任意 shell 命令 (120s 超时) |
| `read_file` | 读取文件, 带行号 |
| `write_file` | 写入/覆盖文件, 自动建目录 |
| `list_dir` | 列出目录内容 |
| `grep` | 正则搜索文件内容 |

## 扩展新工具

在 `harness/tools.py` 里加一个函数:

```python
@tool(
    "git_status",
    "查看 git 状态。",
    {"type": "object", "properties": {}},
)
def git_status(cwd):
    return "clean"
```

注册后 agent 会自动拿到该工具的 schema, 无需其他改动。

## 作为库使用

```python
from harness import LLM, Agent

llm = LLM(api_key="sk-xxx", model="deepseek-chat", effort="high")
agent = Agent(llm, cwd="/path/to/project")
result = agent.run("给项目加一个 README")
print(result)
```

## 项目结构

```
supa-agent/
├── main.py            # CLI 入口 (交互模式 + 一次性模式)
├── harness/
│   ├── agent.py       # agent 主循环 (流式 / 工具调用)
│   ├── tools.py       # 工具注册与实现
│   ├── tui.py         # prompt_toolkit 输入框 / 状态栏 / 斜杠补全
│   └── llm.py         # OpenAI 兼容 API 客户端 (流式 + 非流式)
├── .env.example       # 环境变量示例
└── requirements.txt   # 仅 prompt_toolkit
```
