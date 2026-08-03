"""配置文件: ~/.supa/config.json (全局) + <项目>/.supa/config.json (项目, 覆盖全局)。
支持的键: model, base_url, api_key, effort, yolo, bash_allow (追加到 bash 白名单的前缀列表)。
优先级: 命令行参数 > 环境变量 > 项目配置 > 全局配置。"""
import json
from pathlib import Path


def load_config(cwd):
    merged = {}
    for p in (Path.home() / ".supa" / "config.json", Path(cwd) / ".supa" / "config.json"):
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            merged.update(data)
    return merged
