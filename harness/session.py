"""会话持久化: 每轮结束落盘 ~/.supa/sessions/<id>.json, 支持 --resume 恢复。"""
import json
import time
from pathlib import Path

SESSIONS_DIR = Path.home() / ".supa" / "sessions"


def new_id():
    return time.strftime("%Y%m%d-%H%M%S")


def save(agent):
    try:
        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        data = {
            "model": agent.llm.model,
            "cwd": agent.cwd,
            "messages": agent.messages,
            "todos": agent.todos,
            "total_usage": agent.total_usage,
            "ts": time.time(),
        }
        (SESSIONS_DIR / f"{agent.session_id}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass  # 持久化失败不影响任务


def list_sessions(limit=10):
    if not SESSIONS_DIR.is_dir():
        return []
    out = []
    for f in sorted(SESSIONS_DIR.glob("*.json"), reverse=True)[:limit]:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        first = next((m.get("content") or "" for m in d.get("messages", []) if m.get("role") == "user"), "")
        out.append({"id": f.stem, "cwd": d.get("cwd", ""), "preview": first[:60].replace("\n", " ")})
    return out


def load(agent, session_id):
    d = json.loads((SESSIONS_DIR / f"{session_id}.json").read_text(encoding="utf-8"))
    agent.messages = d["messages"]
    agent.todos = d.get("todos", [])
    agent.total_usage = d.get("total_usage", {})
    if d.get("cwd") and Path(d["cwd"]).is_dir():
        agent.cwd = d["cwd"]
    agent.session_id = session_id
    return d
