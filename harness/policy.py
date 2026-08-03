"""工具执行权限策略: 只读放行, 修改类询问, bash 按命令白名单, --yolo 全放行。"""

# 首词命中即放行 (无 shell 元字符时)
SAFE_COMMANDS = {
    "ls", "cat", "head", "tail", "wc", "pwd", "echo", "which", "file", "du", "df",
    "find", "grep", "rg", "tree", "stat", "diff", "sort", "uniq", "date", "env",
}
# 多词安全前缀
SAFE_PREFIXES = (
    "git status", "git log", "git diff", "git show", "git branch", "git remote -v",
    "go test", "go build", "go vet", "python3 -m pytest", "pytest", "npm test", "make test",
)
# 带这些元字符的命令一律询问 (管道/重定向可能接危险操作)
_SHELL_META = (";", "&&", "||", "|", ">", "<", "`", "$(")


class Policy:
    def __init__(self, yolo=False):
        self.yolo = yolo
        self.session_allowed = set()  # "工具名" 或 "bash:首词" (用户答 a 后记住)

    @staticmethod
    def _bash_key(command):
        words = command.strip().split()
        return f"bash:{words[0]}" if words else "bash:"

    def check(self, name, args, readonly):
        """返回 'allow' 或 'ask'。"""
        if self.yolo or readonly:
            return "allow"
        if name == "bash":
            cmd = args.get("command", "").strip()
            first = cmd.split()[0] if cmd.split() else ""
            safe = first in SAFE_COMMANDS or any(cmd.startswith(p) for p in SAFE_PREFIXES)
            if safe and not any(m in cmd for m in _SHELL_META):
                return "allow"
            if self._bash_key(cmd) in self.session_allowed:
                return "allow"
            return "ask"
        if name in self.session_allowed:
            return "allow"
        return "ask"

    def remember(self, name, args):
        """用户答 'a' (always): 本会话内不再询问同类操作。"""
        self.session_allowed.add(self._bash_key(args.get("command", "")) if name == "bash" else name)
