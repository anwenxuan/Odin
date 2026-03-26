"""
tools/builtin/git_ops.py — Git 操作工具

工具：
- git_clone : 克隆远程仓库到本地临时目录
- git_log   : 查看 git 提交历史
- git_diff  : 查看文件变更（当前版本 vs HEAD / 指定 commit）
"""

from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from tools.base import Tool, ToolResult, ToolContext
from tools.registry import tool

_DEFAULT_TIMEOUT = 120   # clone 默认超时 2 分钟


# ─────────────────────────────────────────────────────────────────────────────
# 共享工具函数
# ─────────────────────────────────────────────────────────────────────────────

def _git_run(args: list[str], cwd: Path | None = None, timeout: int = 30) -> subprocess.CompletedProcess:
    """执行 git 命令的辅助函数。"""
    return subprocess.run(
        ["git"] + args,
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd else None,
        timeout=timeout,
    )


# ─────────────────────────────────────────────────────────────────────────────
# GitCloneTool
# ─────────────────────────────────────────────────────────────────────────────

@tool
class GitCloneTool:
    """克隆远程 Git 仓库到本地临时目录。"""

    name = "git_clone"
    description = (
        "将远程 GitHub/GitLab 仓库克隆到本地临时目录。\n"
        "返回克隆的本地路径，后续可用 read_file / list_dir 等工具操作仓库内容。\n"
        "支持 https:// 和 git@ 开头的 URL。"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": (
                    "Git 仓库 URL。\n"
                    "示例：\n"
                    "  - https://github.com/owner/repo\n"
                    "  - https://github.com/owner/repo.git\n"
                    "  - git@github.com:owner/repo.git\n"
                ),
            },
            "branch": {
                "type": "string",
                "description": "指定克隆的分支（可选，默认 main 或 master）。",
            },
            "shallow": {
                "type": "boolean",
                "description": "浅克隆，只拉取最新提交（默认 True，节省时间和空间）。",
                "default": True,
            },
        },
        "required": ["url"],
    }

    def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        url = args.get("url", "").strip()
        if not url:
            return ToolResult.err("url 参数不能为空")

        # 解析 URL
        parsed = self._parse_url(url)
        if parsed is None:
            return ToolResult.err(f"无法识别的 Git URL 格式：{url}")

        # 确定克隆目标目录
        clone_dir = Path(tempfile.mkdtemp(prefix="odin_repo_"))
        depth_arg = ["--depth=1"] if args.get("shallow", True) else []
        branch_arg = ["--branch", args["branch"]] if args.get("branch") else []

        # 执行克隆
        cmd = ["git", "clone"] + depth_arg + branch_arg + [url, str(clone_dir)]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=int(args.get("timeout", _DEFAULT_TIMEOUT)),
            )
        except subprocess.TimeoutExpired:
            return ToolResult.err(f"克隆超时（{_DEFAULT_TIMEOUT}s），仓库可能较大，考虑使用 shallow=True")

        if result.returncode != 0:
            return ToolResult.err(
                f"git clone 失败（exit {result.returncode}）：{result.stderr[:500]}"
            )

        # 获取克隆的分支名和 commit
        commit = ""
        branch = ""
        try:
            proc = _git_run(["rev-parse", "--short", "HEAD"], cwd=clone_dir)
            if proc.returncode == 0:
                commit = proc.stdout.strip()
            proc = _git_run(["branch", "--show-current"], cwd=clone_dir)
            if proc.returncode == 0:
                branch = proc.stdout.strip()
        except Exception:
            pass

        owner, repo = parsed
        return ToolResult.ok(
            f"仓库克隆成功！\n\n"
            f"  Owner: {owner}\n"
            f"  Repo:  {repo}\n"
            f"  Branch: {branch or '(default)'}\n"
            f"  Commit: {commit}\n"
            f"  Local:  {clone_dir}\n\n"
            f"现在可以用 read_file、list_dir、search_code 等工具分析此仓库。",
            local_path=str(clone_dir),
            owner=owner,
            repo=repo,
            branch=branch,
            commit=commit,
        )

    def _parse_url(self, url: str) -> tuple[str, str] | None:
        """从 URL 中提取 owner 和 repo。"""
        # https://github.com/owner/repo
        m = re.match(r"https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$", url)
        if m:
            return m.group(1), m.group(2)
        # git@github.com:owner/repo.git
        m = re.match(r"git@[^:]+:([^/]+)/([^/]+?)(?:\.git)?$", url)
        if m:
            return m.group(1), m.group(2)
        # https://gitlab.com/...
        m = re.match(r"https?://[^/]+/([^/]+)/([^/]+?)(?:\.git)?/?$", url)
        if m:
            return m.group(1), m.group(2)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# GitLogTool
# ─────────────────────────────────────────────────────────────────────────────

@tool
class GitLogTool:
    """查看 Git 提交历史。"""

    name = "git_log"
    description = (
        "查看 Git 仓库的提交历史，了解代码变更脉络。\n"
        "用途：\n"
        "  - 查看最近 N 次提交（--oneline 格式，一行一个提交）\n"
        "  - 查看某个文件的修改历史\n"
        "  - 了解谁是主要的维护者\n"
        "  - 定位某次引入问题的提交"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "查看特定文件的提交历史（相对于仓库根）。留空则查看全仓库。",
            },
            "n": {
                "type": "integer",
                "description": "显示最近 N 条提交（默认 20）。",
                "default": 20,
            },
            "format": {
                "type": "string",
                "description": "提交格式。默认 '%h %s'（简写 + 主题）。可选：'%H %an %s'（完整 hash + 作者 + 主题）。",
                "default": "%h %s",
            },
        },
        "required": [],
    }

    def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        if ctx.repo_path is None:
            return ToolResult.err("repo_path 未设置")

        n = int(args.get("n", 20))
        fmt = args.get("format", "%h %s")
        rel_path = args.get("path", "").strip()

        cmd = [
            "log",
            f"--format={fmt}",
            f"-{n}",
        ]
        if rel_path:
            cmd.append("--")
            cmd.append(rel_path)

        result = _git_run(cmd, cwd=ctx.repo_path)
        if result.returncode != 0:
            return ToolResult.err(f"git log 失败：{result.stderr[:300]}")

        output = result.stdout
        if not output.strip():
            return ToolResult.ok("没有提交记录（仓库可能为空）")

        header = f"=== Git Log ({fmt}) ==="
        if rel_path:
            header += f" — {rel_path}"
        header += f"\nShowing last {n} commit(s)\n\n"

        return ToolResult.ok(header + output, commit_count=n, path=rel_path)


# ─────────────────────────────────────────────────────────────────────────────
# GitDiffTool
# ─────────────────────────────────────────────────────────────────────────────

@tool
class GitDiffTool:
    """查看文件或提交的代码变更。"""

    name = "git_diff"
    description = (
        "查看 Git 仓库中的代码变更（diff）。\n"
        "用途：\n"
        "  - git_diff()           → 查看当前未提交的变更（Working Directory vs Stage）\n"
        "  - git_diff(ref)        → 查看 HEAD vs 指定引用之间的变更\n"
        "  - git_diff(ref1, ref2) → 查看两个引用之间的变更\n"
        "  - git_diff(path='src/') → 只看某个目录/文件的变更\n\n"
        "在代码审计中：查看 pull request 引入的变更是最常用的手段。"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "base": {
                "type": "string",
                "description": "比较的基准引用（如 'HEAD~1'、'main'）。留空则与 HEAD 比较。",
            },
            "head": {
                "type": "string",
                "description": "比较的头引用（如 'HEAD'、'origin/main'）。留空则显示工作区变更。",
            },
            "path": {
                "type": "string",
                "description": "只查看特定文件/目录的变更（相对于仓库根）。",
            },
            "stat_only": {
                "type": "boolean",
                "description": "只显示变更统计（一行一个文件 + +/- 行数），不显示具体 diff（默认 False）。",
                "default": False,
            },
        },
        "required": [],
    }

    def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        if ctx.repo_path is None:
            return ToolResult.err("repo_path 未设置")

        base = args.get("base", "").strip()
        head = args.get("head", "").strip()
        path = args.get("path", "").strip()
        stat_only = bool(args.get("stat_only", False))

        cmd = ["diff"]
        if stat_only:
            cmd.append("--stat")
        if base:
            cmd.append(base)
        if head:
            cmd.append(head)
        if path:
            cmd.append("--")
            cmd.append(path)

        result = _git_run(cmd, cwd=ctx.repo_path)
        if result.returncode not in (0, 1):
            return ToolResult.err(f"git diff 失败：{result.stderr[:300]}")

        output = result.stdout
        if not output.strip():
            return ToolResult.ok(
                f"无变更{'（使用 --stat_only）' if stat_only else ''}"
                + (f" — path: {path}" if path else "")
            )

        label = "变更统计" if stat_only else "代码变更"
        header = f"=== Git Diff: {label} ===\n"
        if base:
            header += f"Base: {base}"
        if head:
            header += f" ← Head: {head}"
        header += "\n"
        if path:
            header += f"Path: {path}\n"
        header += "\n"

        return ToolResult.ok(
            header + output,
            base=base or "working-tree",
            head=head or "HEAD",
            path=path,
            has_changes=True,
        )
