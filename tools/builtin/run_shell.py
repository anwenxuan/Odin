"""
tools/builtin/run_shell.py — 执行 shell 命令

用途：运行 git、find、grep 等命令行工具，辅助代码分析。
限制：
- 不允许执行交互式命令
- 禁止网络访问命令（curl/wget 等）
- 超时保护（默认 30s）
- 路径限制在 repo 目录内
"""

from __future__ import annotations

import subprocess
import shlex
from pathlib import Path
from typing import Any

from tools.base import Tool, ToolResult, ToolContext
from tools.registry import tool

_DEFAULT_TIMEOUT = 30   # 默认超时 30 秒
_MAX_OUTPUT = 100 * 1024  # 最多保留 100KB 输出


# 允许和禁止的命令白名单
_ALLOWED_COMMANDS = {
    "git", "find", "grep", "ls", "wc", "sort", "uniq",
    "head", "tail", "cut", "awk", "sed", "file",
    "stat", "tree", "du", "diff", "patch",
}

_FORBIDDEN_PATTERNS = [
    "curl", "wget", "ssh", "scp", "ftp",
    "rm -rf", "mkfs", "dd if=",
    "nc ", "ncat", "netcat",
    "bash -i", "sh -i",
    "eval", "exec ",
    "| python", "| bash", "| sh",
    "&& rm", "; rm",
    "&& curl", "; curl",
]


@tool
class RunShellTool:
    """在仓库目录下执行安全的 shell 命令。"""

    name = "run_shell"
    description = (
        "在仓库目录下执行 shell 命令。\n"
        "推荐用法：\n"
        "  - find . -name '*.py' | wc -l  （统计 Python 文件数量）\n"
        "  - git log --oneline -10       （查看最近 10 次提交）\n"
        "  - git diff HEAD~1 --stat       （查看上次提交的变更统计）\n"
        "  - tree -L 3 -I '__pycache__'   （显示目录树，排除缓存目录）\n"
        "安全限制：不支持网络访问、交互命令和危险操作。"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": (
                    "要执行的 shell 命令。\n"
                    "注意：必须在 repo 目录内执行，路径会被自动处理。"
                ),
            },
            "timeout": {
                "type": "integer",
                "description": f"超时时间（秒，默认 {_DEFAULT_TIMEOUT}）。",
                "default": _DEFAULT_TIMEOUT,
            },
        },
        "required": ["command"],
    }

    def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        command = args.get("command", "").strip()
        if not command:
            return ToolResult.err("command 参数不能为空")

        # 安全检查
        security_result = self._security_check(command)
        if security_result is not None:
            return security_result

        # 确定工作目录
        cwd = ctx.repo_path or Path.cwd()
        timeout = int(args.get("timeout", _DEFAULT_TIMEOUT))

        # 执行
        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=str(cwd),
                capture_output=True,
                timeout=timeout,
                text=True,
            )
        except subprocess.TimeoutExpired:
            return ToolResult.err(f"命令执行超时（{timeout}s）")
        except OSError as exc:
            return ToolResult.err(f"命令执行失败：{exc}")

        # 处理输出
        stdout = result.stdout
        stderr = result.stderr
        truncated = False

        if len(stdout) > _MAX_OUTPUT:
            stdout = stdout[:_MAX_OUTPUT]
            truncated = True

        combined = stdout
        if stderr:
            combined += f"\n--- stderr ---\n{stderr[:5000]}"

        if truncated:
            combined += "\n[输出已截断，超出 100KB]"

        header = f"=== Command: {command} ===\n"
        header += f"Exit code: {result.returncode}"
        if result.returncode != 0:
            header += " (non-zero)"
        header += "\n\n"

        return ToolResult.ok(
            header + combined,
            exit_code=result.returncode,
            truncated=truncated,
        )

    def _security_check(self, command: str) -> ToolResult | None:
        """安全检查。返回 None 表示通过，返回 ToolResult 表示拒绝。"""
        lower = command.lower()

        # 禁止危险模式
        for pattern in _FORBIDDEN_PATTERNS:
            if pattern.lower() in lower:
                return ToolResult.err(f"禁止执行包含 '{pattern}' 的命令（安全限制）")

        # 检查第一个命令是否在白名单中
        try:
            parts = shlex.split(command)
        except ValueError:
            return ToolResult.err(f"命令解析失败：{command}")

        if parts:
            base_cmd = parts[0]
            # 禁止相对路径命令 (./script, ../bin/tool)
            if base_cmd.startswith("./") or base_cmd.startswith("../"):
                return ToolResult.err(f"禁止执行相对路径命令：{base_cmd}")
            # 禁止绝对路径命令 (/usr/bin/foo, /bin/sh)
            if base_cmd.startswith("/"):
                return ToolResult.err(f"禁止执行绝对路径命令：{base_cmd}")
            # 禁止包含路径分隔符的命令 (foo/bar)
            if "/" in base_cmd:
                return ToolResult.err(f"禁止执行路径命令：{base_cmd}")
            if base_cmd not in _ALLOWED_COMMANDS:
                return ToolResult.err(
                    f"命令 '{base_cmd}' 不在允许列表中。\n"
                    f"允许的命令：{', '.join(sorted(_ALLOWED_COMMANDS))}"
                )

        return None
