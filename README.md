# coding agent harness

一个零依赖 (纯 Python 标准库) 的 coding agent 框架:给模型一套工具 (bash / read_file / write_file / list_dir / grep),让它自主完成编码任务。兼容任何 OpenAI-format 的 API (DeepSeek / OpenAI / Ollama / vLLM 等)。

## 快速开始

```bash
# 配置 (也可以用 --api-key / --base-url / --model 参数)
export LLM_API_KEY=sk-xxx            # 必填
export LLM_BASE_URL=https://api.deepseek.com/v1   # 可选, 默认 DeepSeek
export LLM_MODEL=deepseek-chat                    # 可选

# 跑一个任务
python3 main.py "看看当前目录有什么文件, 然后写一个 hello.py 并运行它" -d /tmp/demo
```

## 配置

| 环境变量 | 必填 | 默认值 | 说明 |
|---|---|---|---|
| `LLM_API_KEY` | 是 | - | API key |
| `LLM_BASE_URL` | 否 | `https://api.deepseek.com/v1` | OpenAI 兼容 base url |
| `LLM_MODEL` | 否 | `deepseek-chat` | 模型名 |

其他兼容端点示例:

```bash
# OpenAI
export LLM_BASE_URL=https://api.openai.com/v1 LLM_MODEL=gpt-4o-mini
# Ollama (本地)
export LLM_BASE_URL=http://localhost:11434/v1 LLM_MODEL=qwen2.5-coder
# DeepSeek 的 reasoner
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

llm = LLM(api_key="sk-xxx", model="deepseek-chat")
agent = Agent(llm, cwd="/path/to/project")
result = agent.run("给项目加一个 README")
print(result)
```
