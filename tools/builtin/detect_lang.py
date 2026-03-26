"""
tools/builtin/detect_lang.py — 检测仓库技术栈

识别仓库使用的编程语言、主要框架和入口文件。
作为 Skill 分析前的"侦察"工具。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from collections import Counter

from tools.base import Tool, ToolResult, ToolContext
from tools.registry import tool


# ─────────────────────────────────────────────────────────────────────────────
# 语言和框架识别
# ─────────────────────────────────────────────────────────────────────────────

_LANG_EXTENSIONS = {
    "Python":      {".py", ".pyw", ".pyx"},
    "JavaScript":  {".js", ".mjs", ".cjs"},
    "TypeScript":  {".ts", ".tsx", ".mts", ".cts"},
    "Go":          {".go"},
    "Rust":        {".rs"},
    "Java":        {".java"},
    "Kotlin":      {".kt", ".kts"},
    "Swift":       {".swift"},
    "C":           {".c", ".h"},
    "C++":         {".cpp", ".cc", ".hpp", ".hh", ".cxx"},
    "C#":          {".cs"},
    "Ruby":        {".rb"},
    "PHP":         {".php"},
    "Scala":       {".scala"},
    "R":           {".r", ".R"},
    "Shell":       {".sh", ".bash"},
    "Zig":         {".zig"},
}

_FRAMEWORK_PATTERNS = {
    "Django":        {"django", "settings.py", "manage.py", "INSTALLED_APPS"},
    "Flask":         {"flask", "app = Flask", "from flask import"},
    "FastAPI":       {"fastapi", "FastAPI()", "uvicorn"},
    "Express":        {"express", "const app = express"},
    "NestJS":        {"@nestjs", "app.module.ts"},
    "Next.js":       {"next", "pages/", "app/", "next.config"},
    "React":         {"react", "import React", "useState", "useEffect"},
    "Vue":           {"vue", "createApp", "OptionsAPIVue"},
    "Gin":           {"gin-gonic", "gin.Default()", "r := gin."},
    "Echo":          {"echo.lab", "e := echo.New()"},
    "Fiber":         {"gofiber", "fiber.New()"},
    "Spring Boot":   {"spring-boot", "SpringApplication", "@SpringBootApplication"},
    "Rails":         {"Rails", "config/routes.rb", "application.rb"},
    "Laravel":       {"laravel", "artisan", "composer.json"},
    "Axum":          {"axum", "axum::Router"},
    "Actix-web":     {"actix-web", "actix_web"},
    "Phoenix":       {"phoenix", "defmodule"},
    "Remix":         {"remix-run", "@remix-run"},
}

_PACKAGE_FILES = {
    "Python":        "requirements.txt",
    "pip":           "pyproject.toml",
    "npm":           "package.json",
    "Go":            "go.mod",
    "Rust":          "Cargo.toml",
    "Java":          "pom.xml",
    "Java (Gradle)": "build.gradle",
    "Maven":         "pom.xml",
    "PHP":           "composer.json",
    "Ruby":          "Gemfile",
    "Swift":         "Package.swift",
    ".NET":          "*.csproj",
    "Elixir":        "mix.exs",
}


@tool
class DetectLangTool:
    """检测仓库使用的编程语言、框架和依赖管理工具。"""

    name = "detect_lang"
    description = (
        "快速扫描仓库，识别技术栈。\n"
        "返回：\n"
        "  - 主要编程语言及文件数量\n"
        "  - 检测到的 Web 框架\n"
        "  - 依赖管理工具（pip/npm/go mod/cargo 等）\n"
        "  - 入口文件猜测\n\n"
        "这是代码分析的第一步，应在深入分析前先调用此工具。"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "扫描路径（相对于仓库根）。留空则扫描全仓库。",
            },
            "deep_scan": {
                "type": "boolean",
                "description": "是否进行深度扫描（搜索配置文件中的框架标识，默认 False）。",
                "default": False,
            },
        },
        "required": [],
    }

    def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        if ctx.repo_path is None:
            return ToolResult.err("repo_path 未设置")

        search_path = ctx.resolve_path(args.get("path", "")) or ctx.repo_path
        if not search_path.exists():
            return ToolResult.err(f"路径不存在：{search_path}")

        deep_scan = bool(args.get("deep_scan", False))

        # 1. 统计各语言文件数
        lang_counts = Counter()
        scanned = 0
        skip_dirs = {
            "node_modules", ".git", "__pycache__", "vendor",
            "target", "dist", "build", ".venv", "venv",
            ".next", ".nuxt", ".cache",
        }

        try:
            for p in search_path.rglob("*"):
                if scanned >= 5000:
                    break
                if p.is_file():
                    rel = str(p.relative_to(search_path))
                    parts = rel.split("/")
                    if any(sd in parts for sd in skip_dirs):
                        continue
                    ext = p.suffix.lower()
                    for lang, exts in _LANG_EXTENSIONS.items():
                        if ext in exts:
                            lang_counts[lang] += 1
                            break
                    scanned += 1
        except PermissionError:
            pass

        # 2. 排序语言
        top_langs = lang_counts.most_common(10)
        if not top_langs:
            return ToolResult.ok(
                "未检测到常见编程语言文件。仓库可能为空或使用了非标准文件。",
                languages=[],
                frameworks=[],
            )

        # 3. 框架检测（深度扫描时）
        frameworks = []
        if deep_scan:
            try:
                for p in search_path.rglob("*.py"):
                    if "requirements.txt" in str(p) or "setup.py" in str(p):
                        frameworks.extend(self._detect_from_file(p))
                for p in search_path.rglob("package.json"):
                    frameworks.extend(self._detect_from_file(p))
                for p in search_path.rglob("go.mod"):
                    frameworks.append("Go module")
            except PermissionError:
                pass
            frameworks = list(dict.fromkeys(frameworks))  # 去重保序

        # 4. 依赖管理工具检测
        dep_tools = []
        for tool_name, marker_file in _PACKAGE_FILES.items():
            marker = Path(search_path) / marker_file
            if marker.is_file():
                dep_tools.append(tool_name)
            elif "*" in marker_file:
                # handle glob patterns like *.csproj
                try:
                    hits = list(search_path.glob(marker_file))
                    if hits:
                        dep_tools.append(tool_name)
                except OSError:
                    pass

        # 5. 入口文件猜测
        entry_guesses = self._guess_entry_points(search_path, top_langs)

        # 6. 框架猜测（基于目录名和语言）
        if not deep_scan and top_langs:
            guessed = self._guess_framework(search_path, top_langs[0][0])
            if guessed:
                frameworks = [guessed] + frameworks

        # 格式化输出
        lines = [
            "=== Repository Tech Stack ===",
            "",
            "Languages (by file count):",
        ]
        for lang, count in top_langs:
            bar = "█" * min(count, 30)
            lines.append(f"  {lang:<15} {count:>6}  {bar}")
        lines.append("")

        if frameworks:
            lines.append("Frameworks detected:")
            for fw in frameworks:
                lines.append(f"  - {fw}")
            lines.append("")

        if dep_tools:
            lines.append("Dependency tools:")
            for tool in dep_tools:
                lines.append(f"  - {tool}")
            lines.append("")

        if entry_guesses:
            lines.append("Likely entry points:")
            for ep in entry_guesses[:8]:
                lines.append(f"  - {ep}")

        return ToolResult.ok(
            "\n".join(lines),
            languages=dict(top_langs),
            frameworks=frameworks,
            dependency_tools=dep_tools,
            entry_points=entry_guesses,
        )

    def _detect_from_file(self, path: Path) -> list[str]:
        try:
            content = path.read_text(encoding="utf-8", errors="replace")[:5000]
            found = []
            for fw, markers in _FRAMEWORK_PATTERNS.items():
                if any(m in content for m in markers):
                    found.append(fw)
            return found
        except OSError:
            return []

    def _guess_framework(self, root: Path, primary_lang: str) -> str | None:
        """基于目录名猜测框架。"""
        fw_by_dir = {
            "django": "Django",
            "flask": "Flask",
            "fastapi": "FastAPI",
            "express": "Express",
            "nest": "NestJS",
            "gin": "Gin",
            "echo": "Echo",
            "fiber": "Fiber",
            "rails": "Rails",
            "laravel": "Laravel",
            "spring": "Spring Boot",
            "phoenix": "Phoenix",
        }
        for name, fw in fw_by_dir.items():
            if (root / name).is_dir():
                return fw
        return None

    def _guess_entry_points(self, root: Path, lang_counts: list[tuple[str, int]]) -> list[str]:
        """猜测入口文件。"""
        guesses = []
        lang = lang_counts[0][0] if lang_counts else ""

        candidates = {
            "Python":  ["main.py", "app.py", "run.py", "server.py", "cli.py", "manage.py"],
            "JavaScript": ["index.js", "app.js", "server.js", "main.js"],
            "TypeScript": ["index.ts", "app.ts", "server.ts", "main.ts"],
            "Go":        ["main.go", "cmd/main.go", "cmd/server/main.go", "cmd/cli/main.go"],
            "Rust":      ["main.rs", "src/main.rs", "src/bin/"],
            "Java":      ["src/main/java/", "Main.java", "src/App.java"],
            "Ruby":      ["app.rb", "main.rb", "config.ru"],
            "PHP":       ["index.php", "public/index.php"],
            "Shell":     ["main.sh", "run.sh", "setup.sh"],
        }

        for c in candidates.get(lang, []):
            if c.endswith("/"):
                try:
                    hits = list(root.rglob(c.rstrip("/")))
                    for h in hits:
                        guesses.append(str(h.relative_to(root)))
                except OSError:
                    pass
            else:
                p = root / c
                if p.is_file():
                    guesses.append(c)

        return guesses
