"""
cli/main.py — Odin AI Code Research CLI

用法：
    odin analyze <repo-url-or-path> [options]
    odin list-skills
    odin list-workflows
    odin --version

环境变量：
    OPENAI_API_KEY       — OpenAI API Key（可选）
    ANTHROPIC_API_KEY    — Anthropic API Key（可选）
    ODIN_PROVIDER        — LLM 提供商：openai | anthropic | ollama | mock
    ODIN_MODEL           — 模型名称（默认 gpt-4o-mini）
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

# 确保项目根在 Python 路径中
_PROJECT_ROOT = Path(__file__).parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from cli.commands.analyze import analyze_command
from cli.commands.list_skills import list_skills_command
from cli.commands.list_workflows import list_workflows_command

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("odin")


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="odin",
        description="Odin — AI Code Research System",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--version",
        action="version",
        version="Odin v0.2.0",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # ── analyze 命令 ────────────────────────────────────────────────────────
    p_analyze = subparsers.add_parser(
        "analyze",
        help="分析 GitHub 仓库或本地代码库",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=analyze_command.__doc__ or "",
    )
    _setup_analyze_parser(p_analyze)

    # ── list-skills 命令 ────────────────────────────────────────────────────
    p_skills = subparsers.add_parser(
        "list-skills",
        help="列出所有已注册的 Skills",
    )

    # ── list-workflows 命令 ─────────────────────────────────────────────────
    p_wf = subparsers.add_parser(
        "list-workflows",
        help="列出所有已注册的 Workflows",
    )

    args = parser.parse_args()

    try:
        if args.command == "analyze":
            return _run_analyze(args)
        elif args.command == "list-skills":
            return list_skills_command(args)
        elif args.command == "list-workflows":
            return list_workflows_command(args)
        else:
            parser.print_help()
            return 1
    except KeyboardInterrupt:
        logger.info("操作已取消")
        return 130
    except Exception as exc:  # noqa: BLE001
        logger.exception("CLI 错误: %s", exc)
        return 1


def _setup_analyze_parser(p: argparse.ArgumentParser) -> None:
    """配置 analyze 命令的参数解析器。"""
    p.add_argument(
        "repo",
        help="GitHub URL（如 https://github.com/owner/repo）或本地路径",
    )
    p.add_argument(
        "--workflow",
        choices=["vulnerability_research", "codebase_research", "architecture_analysis"],
        default="codebase_research",
        help="使用的分析工作流（默认 codebase_research）",
    )
    p.add_argument(
        "--provider",
        choices=["openai", "anthropic", "ollama", "mock"],
        default=os.environ.get("ODIN_PROVIDER", "openai"),
        help="LLM 提供商（默认从 OIDC_PROVIDER 环境变量读取）",
    )
    p.add_argument(
        "--model",
        default=os.environ.get("ODIN_MODEL", "gpt-4o-mini"),
        help="模型名称（默认 gpt-4o-mini）",
    )
    p.add_argument(
        "--output",
        type=Path,
        help="输出报告路径（默认输出到 stdout）",
    )
    p.add_argument(
        "--output-format",
        choices=["markdown", "json"],
        default="markdown",
        help="报告格式（默认 markdown）",
    )
    p.add_argument(
        "--focus-paths",
        nargs="*",
        help="重点分析的路径（相对于仓库根）",
    )
    p.add_argument(
        "--max-iterations",
        type=int,
        default=20,
        help="单个 Skill 的最大工具调用次数（默认 20）",
    )
    p.add_argument(
        "--verbose",
        action="store_true",
        help="打印详细执行日志",
    )
    p.add_argument(
        "--branch",
        help="克隆仓库时指定的分支（仅对 GitHub URL 有效）",
    )
    p.add_argument(
        "--no-cache",
        action="store_true",
        help="不使用本地缓存，重新克隆仓库",
    )
    p.add_argument(
        "--serve",
        action="store_true",
        help="启动 FastAPI API Server 模式",
    )
    p.add_argument(
        "--serve-port",
        type=int,
        default=8080,
        help="API Server 端口（默认 8080）",
    )


def _run_analyze(args: argparse.Namespace) -> int:
    """执行 analyze 命令。"""
    if args.serve:
        from cli.commands.serve import run_server
        run_server(port=args.serve_port)
        return 0

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # 验证 repo 参数
    repo_input = args.repo.strip()
    is_github = repo_input.startswith("http://") or repo_input.startswith("https://") or repo_input.startswith("git@")

    if is_github:
        logger.info("分析 GitHub 仓库: %s", repo_input)
    else:
        p = Path(repo_input).resolve()
        if not p.exists():
            logger.error("本地路径不存在: %s", p)
            return 1
        logger.info("分析本地代码库: %s", p)

    # 执行分析
    return analyze_command(args)


if __name__ == "__main__":
    sys.exit(main())
