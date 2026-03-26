"""
tests/agent/test_sandbox.py — Sandbox & Tool Router Integration Tests

Imports bypass agent/__init__.py to avoid cascade import issues with existing code.
"""
# Import submodules directly to bypass agent/__init__.py
import sys, os
_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, _root)

import pytest
import asyncio

from agent.sandbox import (
    SandboxConfig,
    SandboxMode,
    SandboxResult,
    SandboxGateway,
    SandboxAuditLogger,
    ProcessSandbox,
)
from tools.router import (
    ToolRouter,
    ToolCategory,
    ToolMetadata,
    ToolIntent,
)
from agent.llm_adapter import MockAdapter


class TestSandboxConfig:
    def test_defaults(self):
        cfg = SandboxConfig()
        assert cfg.mode == SandboxMode.PROCESS
        assert cfg.pool_size == 3
        assert cfg.timeout == 300.0
        assert cfg.cpu_limit == 1.0
        assert cfg.memory_limit == "512m"
        assert cfg.network_enabled is False

    def test_docker_mode(self):
        cfg = SandboxConfig(
            mode=SandboxMode.DOCKER,
            docker_image="python:3.11-slim",
        )
        assert cfg.mode == SandboxMode.DOCKER
        assert cfg.docker_image == "python:3.11-slim"


class TestSandboxResult:
    def test_success_result(self):
        result = SandboxResult(
            success=True,
            stdout="hello world",
            stderr="",
            exit_code=0,
            duration_ms=150,
        )
        assert result.success is True
        assert result.exit_code == 0

    def test_failure_result(self):
        result = SandboxResult(
            success=False,
            stdout="",
            stderr="error: command not found",
            exit_code=1,
            duration_ms=50,
            error="command not found",
        )
        assert result.success is False
        assert result.error == "command not found"

    def test_to_dict(self):
        result = SandboxResult(success=True, stdout="ok", duration_ms=100)
        d = result.to_dict()
        assert d["success"] is True
        assert d["duration_ms"] == 100


class TestSandboxAuditLogger:
    def test_init(self, tmp_path):
        logger = SandboxAuditLogger(log_dir=tmp_path)
        assert logger.log_dir.exists()

    def test_log(self, tmp_path):
        logger = SandboxAuditLogger(log_dir=tmp_path)
        result = SandboxResult(success=True, stdout="test", duration_ms=50)
        logger.log("session-001", "test_tool", {"arg": "value"}, result)

        log_file = logger.log_dir / "session-001.jsonl"
        assert log_file.exists()
        content = log_file.read_text()
        assert "test_tool" in content
        assert "session-001" in content


class TestProcessSandbox:
    def test_init(self, tmp_path):
        config = SandboxConfig(mode=SandboxMode.PROCESS, timeout=10.0)
        logger = SandboxAuditLogger(log_dir=tmp_path)
        sandbox = ProcessSandbox("test-session", config, logger)
        assert sandbox.session_id == "test-session"
        assert sandbox._workdir.exists()

    @pytest.mark.asyncio
    async def test_run_simple_command(self, tmp_path):
        config = SandboxConfig(mode=SandboxMode.PROCESS, timeout=10.0)
        logger = SandboxAuditLogger(log_dir=tmp_path)
        sandbox = ProcessSandbox("test-run", config, logger)

        result = await sandbox.run(
            tool_id="test_tool",
            params={"cmd": "echo hello"},
            script="echo hello",
        )

        assert result.success is True
        assert "hello" in result.stdout
        sandbox.cleanup()

    @pytest.mark.asyncio
    async def test_run_failure(self, tmp_path):
        config = SandboxConfig(mode=SandboxMode.PROCESS, timeout=10.0)
        logger = SandboxAuditLogger(log_dir=tmp_path)
        sandbox = ProcessSandbox("test-fail", config, logger)

        result = await sandbox.run(
            tool_id="test_tool",
            params={},
            script="exit 1",
        )

        assert result.success is False
        sandbox.cleanup()


class TestSandboxGateway:
    @pytest.mark.asyncio
    async def test_init(self, tmp_path):
        gateway = SandboxGateway(SandboxConfig(mode=SandboxMode.PROCESS))
        stats = gateway.get_stats()
        assert stats["mode"] == "process"
        assert stats["active_process_sandboxes"] == 0

    @pytest.mark.asyncio
    async def test_get_sandbox(self, tmp_path):
        config = SandboxConfig(mode=SandboxMode.PROCESS, timeout=10.0)
        gateway = SandboxGateway(config)

        sandbox = await gateway.get_sandbox("session-001")
        assert sandbox is not None
        assert isinstance(sandbox, ProcessSandbox)

        # Second call returns same instance
        sandbox2 = await gateway.get_sandbox("session-001")
        assert sandbox is sandbox2

        await gateway.cleanup_all()

    @pytest.mark.asyncio
    async def test_execute_command(self, tmp_path):
        config = SandboxConfig(mode=SandboxMode.PROCESS, timeout=10.0)
        gateway = SandboxGateway(config)

        result = await gateway.execute_command(
            "echo 'gateway test'",
            session_id="gateway-session",
        )

        assert result.success is True
        assert "gateway test" in result.stdout

        await gateway.cleanup_all()

    @pytest.mark.asyncio
    async def test_cleanup_session(self, tmp_path):
        config = SandboxConfig(mode=SandboxMode.PROCESS)
        gateway = SandboxGateway(config)

        await gateway.get_sandbox("cleanup-session")
        await gateway.cleanup_session("cleanup-session")

        assert "cleanup-session" not in gateway._process_sandboxes


class TestToolMetadata:
    def test_creation(self):
        meta = ToolMetadata(
            id="my_tool",
            name="my_tool",
            description="A test tool",
            category=ToolCategory.FILE,
            parameters={"type": "object", "properties": {}},
            returns={},
            tags=["test", "file"],
        )
        assert meta.id == "my_tool"
        assert meta.category == ToolCategory.FILE
        assert meta.danger_level == "safe"

    def test_to_openai_schema(self):
        meta = ToolMetadata(
            id="read_file",
            name="read_file",
            description="Read a file",
            category=ToolCategory.FILE,
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
            returns={},
        )
        schema = meta.to_openai_schema()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "read_file"

    def test_keyword_score(self):
        meta = ToolMetadata(
            id="read_file",
            name="read_file",
            description="Read a file",
            category=ToolCategory.FILE,
            parameters={},
            returns={},
            score_keywords=["read", "file", "content"],
        )
        assert meta.keyword_score("read the file content") > 0
        assert meta.keyword_score("foobar xyz") == 0


class TestToolRouter:
    def test_init_with_builtins(self):
        adapter = MockAdapter()
        router = ToolRouter(adapter)

        assert len(router._metadata) > 0
        assert router.get("read_file") is not None
        assert router.get("search_code") is not None
        assert router.get("detect_lang") is not None

    def test_list_by_category(self):
        adapter = MockAdapter()
        router = ToolRouter(adapter)

        file_tools = router.list_by_category(ToolCategory.FILE)
        assert len(file_tools) >= 2  # read_file, list_dir

    def test_route_direct(self):
        adapter = MockAdapter()
        router = ToolRouter(adapter)

        intent = router.route_direct("read_file", {"path": "main.py"})
        assert intent is not None
        assert intent.tool_id == "read_file"
        assert intent.confidence == 1.0
        assert intent.category == ToolCategory.FILE

    def test_route_direct_unknown_tool(self):
        adapter = MockAdapter()
        router = ToolRouter(adapter)

        intent = router.route_direct("nonexistent_tool")
        assert intent is None

    def test_route_by_keywords(self):
        adapter = MockAdapter()
        router = ToolRouter(adapter)

        intent = router.route_by_keywords("read the content of config.py")
        assert intent is not None
        assert intent.tool_id == "read_file"

    def test_route_by_keywords_no_match(self):
        adapter = MockAdapter()
        router = ToolRouter(adapter)

        intent = router.route_by_keywords("xyzzyplugh")
        assert intent is None

    def test_requires_sandbox(self):
        adapter = MockAdapter()
        router = ToolRouter(adapter)

        safe_intent = router.route_direct("read_file")
        assert router.requires_sandbox(safe_intent) is False

        shell_intent = router.route_direct("run_shell")
        assert router.requires_sandbox(shell_intent) is True

    def test_register_custom_tool(self):
        adapter = MockAdapter()
        router = ToolRouter(adapter)

        custom = ToolMetadata(
            id="custom_tool",
            name="custom_tool",
            description="Custom tool",
            category=ToolCategory.SEARCH,
            parameters={},
            returns={},
            sandbox_required=True,
            danger_level="dangerous",
        )
        router.register(custom)
        assert router.get("custom_tool") is not None

        intent = router.route_direct("custom_tool")
        assert intent is not None
        assert intent.danger_level == "dangerous"

    def test_mode_values(self):
        assert SandboxMode.PROCESS.value == "process"
        assert SandboxMode.DOCKER.value == "docker"
        assert SandboxMode.VM.value == "vm"
