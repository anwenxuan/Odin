"""
tools/builtin/list_dir.py — 列出目录结构

支持：
- 递归目录树（深度限制）
- 文件过滤（扩展名、目录名）
- 语言统计（Python/JS/Go 文件计数）
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from tools.base import Tool, ToolResult, ToolContext, tool

_MAX_DEPTH = 6        # 默认递归深度
_MAX_ENTRIES = 2000  # 最多显示条目数


@tool
class ListDirTool:
    """列出仓库目录结构，帮助了解代码布局。"""

    name = "list_dir"
    description = (
        "列出指定目录的文件和子目录结构。用于了解仓库布局、找到入口文件、定位关键模块。\n"
        "建议先用 list_dir(root) 看全局结构，再用 list_dir(dir='某子目录') 深入查看。\n"
        "支持文件类型过滤和深度限制。"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "dir": {
                "type": "string",
                "description": "目录路径（相对于仓库根）。留空则列出仓库根目录。",
            },
            "max_depth": {
                "type": "integer",
                "description": f"递归深度（默认 {_MAX_DEPTH}，最大 10）。",
                "default": _MAX_DEPTH,
            },
            "file_filter": {
                "type": "string",
                "description": "只显示匹配此 glob 模式的文件，例如：*.py、*.js（支持逗号分隔多个）。",
            },
            "include_hidden": {
                "type": "boolean",
                "description": "是否包含以 . 开头的隐藏文件/目录（默认 False）。",
                "default": False,
            },
        },
        "required": [],
    }

    def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        if ctx.repo_path is None:
            return ToolResult.err("repo_path 未设置")

        # 解析目录
        rel_dir = args.get("dir", "").strip()
        if rel_dir:
            base = ctx.resolve_path(rel_dir)
        else:
            base = ctx.repo_path

        if base is None or not base.is_dir():
            return ToolResult.err(f"目录不存在或路径不安全：{rel_dir or '/'}")

        max_depth = min(int(args.get("max_depth", _MAX_DEPTH)), 10)
        include_hidden = bool(args.get("include_hidden", False))

        # 文件过滤器
        filters_raw = args.get("file_filter", "")
        filters = []
        if filters_raw:
            for pattern in filters_raw.split(","):
                pattern = pattern.strip()
                if pattern:
                    filters.append(pattern)

        # 构建目录树
        lines, stats = self._build_tree(
            base, max_depth, filters, include_hidden, ctx.repo_path
        )

        output = "\n".join(lines)
        header = f"=== Directory: {rel_dir or '/'} ===\n"
        header += f"Files: {stats['files']} | Dirs: {stats['dirs']}"
        header += f" | Languages: {stats['languages']}"
        header += "\n\n"

        return ToolResult.ok(
            header + output,
            total_files=stats["files"],
            total_dirs=stats["dirs"],
            languages=stats["languages"],
        )

    def _build_tree(
        self,
        root: Path,
        max_depth: int,
        filters: list[str],
        include_hidden: bool,
        repo_root: Path,
    ) -> tuple[list[str], dict[str, Any]]:
        lines: list[str] = []
        stats = {"files": 0, "dirs": 0, "languages": {}}
        entries: list[tuple[Path, int]] = []

        def walk(path: Path, depth: int) -> None:
            if depth > max_depth or stats["files"] + stats["dirs"] > _MAX_ENTRIES:
                return
            try:
                children = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name))
            except PermissionError:
                return

            for child in children:
                name = child.name

                # 隐藏文件过滤
                if not include_hidden and name.startswith("."):
                    continue

                rel = child.relative_to(repo_root)

                # glob 过滤器
                if filters and child.is_file():
                    if not any(self._matches(child.name, f) for f in filters):
                        continue

                prefix = "  " * depth
                if child.is_dir():
                    lines.append(f"{prefix}📁 {name}/")
                    stats["dirs"] += 1
                    entries.append((child, depth + 1))
                else:
                    ext = child.suffix.lower()
                    icon = self._icon_for_ext(ext)
                    lines.append(f"{prefix}{icon} {name}")
                    stats["files"] += 1
                    self._count_lang(stats["languages"], ext)

        walk(root, 0)

        # 按深度分层追加（避免递归中直接修改 lines 导致乱序）
        while entries:
            path, depth = entries.pop(0)
            walk(path, depth)

        return lines, stats

    def _matches(self, filename: str, pattern: str) -> bool:
        """简单的 glob 匹配（支持 * 和 ?）。"""
        regex = pattern.replace(".", r"\.").replace("**", ".*").replace("*", "[^/]*").replace("?", ".")
        try:
            return bool(re.match(f"^{regex}$", filename))
        except re.error:
            return False

    def _icon_for_ext(self, ext: str) -> str:
        icons = {
            ".py": "🐍",
            ".js": "📜",
            ".ts": "📘",
            ".tsx": "📗",
            ".go": "🐹",
            ".rs": "🦀",
            ".java": "☕",
            ".rb": "💎",
            ".php": "🐘",
            ".c": "🔧",
            ".cpp": "🔩",
            ".h": "🔧",
            ".cs": "🟣",
            ".swift": "🍎",
            ".kt": "🤖",
            ".sh": "⚡",
            ".yaml": "📋",
            ".yml": "📋",
            ".json": "📦",
            ".toml": "📋",
            ".md": "📝",
            ".txt": "📄",
            ".sql": "🗃️",
            ".html": "🌐",
            ".css": "🎨",
            ".vue": "💚",
            ".svelte": "🧡",
        }
        return icons.get(ext, "📄")

    def _count_lang(self, langs: dict[str, int], ext: str) -> None:
        mapping = {
            ".py": "Python",
            ".js": "JavaScript",
            ".ts": "TypeScript",
            ".tsx": "TypeScript",
            ".jsx": "JavaScript",
            ".go": "Go",
            ".rs": "Rust",
            ".java": "Java",
            ".rb": "Ruby",
            ".php": "PHP",
            ".c": "C",
            ".cpp": "C++",
            ".cc": "C++",
            ".cs": "C#",
            ".swift": "Swift",
            ".kt": "Kotlin",
            ".sh": "Shell",
        }
        lang = mapping.get(ext)
        if lang:
            langs[lang] = langs.get(lang, 0) + 1
