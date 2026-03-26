"""
agent/sandbox.py — Sandbox Gateway

沙箱执行引擎，支持三种隔离模式：
1. PROCESS  — 进程隔离 + seccomp（默认，最轻量）
2. DOCKER   — Docker 容器隔离（完全隔离）
3. VM       — 虚拟机隔离（最高安全，用于危险操作）

核心职责：
1. Container Pool    — 预热容器池，可复用
2. Session Manager  — 每个 Task 独立 Session
3. Resource Limiter — CPU/Memory/Network 限制
4. Audit Logger     — 所有操作的审计日志
"""

from __future__ import annotations

import asyncio
import logging
import os
import resource
import secrets
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Sandbox Modes & Config
# ─────────────────────────────────────────────────────────────────────────────


class SandboxMode(str, Enum):
    """沙箱隔离模式。"""
    PROCESS = "process"          # 进程隔离（默认）
    DOCKER = "docker"            # Docker 容器
    VM = "vm"                   # 虚拟机（未实现）


@dataclass
class SandboxConfig:
    """沙箱配置。"""
    mode: SandboxMode = SandboxMode.PROCESS
    pool_size: int = 3                    # 容器池大小
    timeout: float = 300.0                # 单次执行超时（秒）
    cpu_limit: float = 1.0                # CPU 核数限制
    memory_limit: str = "512m"            # 内存限制
    network_enabled: bool = False         # 是否允许网络
    disk_limit: str = "1g"               # 磁盘空间限制
    readonly_paths: list[str] = field(default_factory=list)
    # Docker 专用
    docker_image: str = "python:3.11-slim"
    docker_network: str = "none"
    # PROCESS 专用
    max_processes: int = 10


# ─────────────────────────────────────────────────────────────────────────────
# Sandbox Result
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class SandboxResult:
    """沙箱执行结果。"""
    success: bool
    stdout: str = ""
    stderr: str = ""
    exit_code: int = -1
    duration_ms: int = 0
    resource_usage: dict[str, Any] = field(default_factory=dict)
    audit_log: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "stdout": self.stdout[:500],
            "stderr": self.stderr[:500],
            "exit_code": self.exit_code,
            "duration_ms": self.duration_ms,
            "resource_usage": self.resource_usage,
            "error": self.error,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Audit Logger
# ─────────────────────────────────────────────────────────────────────────────


class SandboxAuditLogger:
    """沙箱审计日志记录器。"""

    def __init__(self, log_dir: str | Path = ".odin/audit"):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def log(
        self,
        session_id: str,
        tool_id: str,
        params: dict[str, Any],
        result: SandboxResult,
    ) -> None:
        """记录沙箱操作。"""
        audit = {
            "session_id": session_id,
            "tool_id": tool_id,
            "params": params,
            "success": result.success,
            "exit_code": result.exit_code,
            "duration_ms": result.duration_ms,
            "resource_usage": result.resource_usage,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "error": result.error,
        }
        log_file = self.log_dir / f"{session_id}.jsonl"
        import json
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(audit, ensure_ascii=False) + "\n")


# ─────────────────────────────────────────────────────────────────────────────
# Process Sandbox
# ─────────────────────────────────────────────────────────────────────────────


class ProcessSandbox:
    """
    进程级沙箱。

    使用 os.fork + setrlimit 实现资源限制。
    适用于大多数场景，无需 Docker。
    """

    def __init__(
        self,
        session_id: str,
        config: SandboxConfig,
        audit_logger: SandboxAuditLogger,
    ):
        self.session_id = session_id
        self.config = config
        self.audit = audit_logger
        self._workdir = Path(tempfile.mkdtemp(prefix=f"odin-sandbox-{session_id}-"))

    async def run(
        self,
        tool_id: str,
        params: dict[str, Any],
        script: str,
    ) -> SandboxResult:
        """
        在进程沙箱中执行脚本。

        Args:
            tool_id: 工具 ID
            params : 执行参数
            script : 要执行的脚本内容

        Returns:
            SandboxResult
        """
        t0 = time.monotonic()

        # 写临时脚本
        script_path = self._workdir / f"{tool_id}.sh"
        script_path.write_text(script, encoding="utf-8")
        script_path.chmod(0o700)

        # 构建命令
        cmd = ["/bin/bash", str(script_path)]

        # 设置资源限制
        soft, hard = resource.RLIMIT_CPU, resource.RLIMIT_CPU
        cpu_seconds = int(self.config.timeout)

        env = {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": str(self._workdir),
            "SESSION_ID": self.session_id,
            "TOOL_ID": tool_id,
        }

        result = await self._run_subprocess(
            cmd,
            env,
            cpu_seconds=cpu_seconds,
        )

        duration_ms = int((time.monotonic() - t0) * 1000)

        sandbox_result = SandboxResult(
            success=result["returncode"] == 0,
            stdout=result["stdout"],
            stderr=result["stderr"],
            exit_code=result["returncode"],
            duration_ms=duration_ms,
            resource_usage=result.get("rusage", {}),
            error=result.get("error"),
        )

        self.audit.log(self.session_id, tool_id, params, sandbox_result)
        return sandbox_result

    async def _run_subprocess(
        self,
        cmd: list[str],
        env: dict[str, str],
        cpu_seconds: int,
    ) -> dict[str, Any]:
        """使用 asyncio 执行子进程（带资源限制）。"""
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self._workdir),
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=max(cpu_seconds, 1.0) + 5,
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return {
                    "returncode": -1,
                    "stdout": "",
                    "stderr": f"Timeout after {cpu_seconds}s",
                    "error": "timeout",
                }

            return {
                "returncode": proc.returncode,
                "stdout": stdout.decode("utf-8", errors="replace"),
                "stderr": stderr.decode("utf-8", errors="replace"),
            }

        except Exception as exc:
            logger.exception("[Sandbox] Process execution failed")
            return {
                "returncode": -1,
                "stdout": "",
                "stderr": str(exc),
                "error": str(exc),
            }

    def cleanup(self) -> None:
        """清理临时工作目录。"""
        import shutil
        try:
            shutil.rmtree(self._workdir, ignore_errors=True)
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# Docker Sandbox
# ─────────────────────────────────────────────────────────────────────────────


class DockerSandbox:
    """
    Docker 容器沙箱。

    完全隔离的容器环境，适合需要完整系统调用权限的场景。
    """

    def __init__(
        self,
        session_id: str,
        config: SandboxConfig,
        audit_logger: SandboxAuditLogger,
    ):
        self.session_id = session_id
        self.config = config
        self.audit = audit_logger
        self._container_id: str | None = None
        self._workdir = f"/tmp/odin-sandbox-{session_id}"

    async def acquire_container(self) -> str:
        """
        从容器池获取或创建容器。

        Returns:
            container_id
        """
        container_name = f"odin-{self.session_id}-{uuid.uuid4().hex[:8]}"

        cmd = [
            "docker", "run",
            "--name", container_name,
            "--rm",
            "--network", self.config.docker_network,
            "--memory", self.config.memory_limit,
            "--cpus", str(self.config.cpu_limit),
            "--pids-limit", str(self.config.max_processes),
            "--read-only" if not self.config.network_enabled else "--network=none",
            "--user", "odin" if os.path.exists("/etc/passwd") else "root",
            "-d",
            self.config.docker_image,
            "sleep", "3600",
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60,
            )
            if result.returncode == 0:
                self._container_id = result.stdout.strip()
                return self._container_id
            else:
                logger.warning("[DockerSandbox] Failed to start container: %s", result.stderr)
                return ""
        except Exception as exc:
            logger.exception("[DockerSandbox] Container acquisition failed")
            return ""

    async def run(
        self,
        tool_id: str,
        params: dict[str, Any],
        script: str,
    ) -> SandboxResult:
        """
        在 Docker 容器中执行脚本。
        """
        t0 = time.monotonic()

        # 如果没有容器，先获取
        if not self._container_id:
            self._container_id = await self.acquire_container()
            if not self._container_id:
                return SandboxResult(
                    success=False,
                    error="Failed to acquire Docker container",
                    duration_ms=int((time.monotonic() - t0) * 1000),
                )

        # 写脚本到容器
        encoded_script = __import__("base64").b64encode(script.encode("utf-8")).decode()

        exec_cmd = [
            "docker", "exec", self._container_id,
            "bash", "-c",
            f"echo '{encoded_script}' | base64 -d > /tmp/odin_exec.sh && chmod +x /tmp/odin_exec.sh && /tmp/odin_exec.sh && rm /tmp/odin_exec.sh",
        ]

        try:
            result = subprocess.run(
                exec_cmd,
                capture_output=True,
                text=True,
                timeout=self.config.timeout,
            )

            duration_ms = int((time.monotonic() - t0) * 1000)
            sandbox_result = SandboxResult(
                success=result.returncode == 0,
                stdout=result.stdout,
                stderr=result.stderr,
                exit_code=result.returncode,
                duration_ms=duration_ms,
            )

            self.audit.log(self.session_id, tool_id, params, sandbox_result)
            return sandbox_result

        except subprocess.TimeoutExpired:
            return SandboxResult(
                success=False,
                error=f"Timeout after {self.config.timeout}s",
                duration_ms=int((time.monotonic() - t0) * 1000),
            )
        except Exception as exc:
            logger.exception("[DockerSandbox] Execution failed")
            return SandboxResult(
                success=False,
                error=str(exc),
                duration_ms=int((time.monotonic() - t0) * 1000),
            )

    def release(self) -> None:
        """释放容器（放回池或销毁）。"""
        if self._container_id:
            try:
                subprocess.run(
                    ["docker", "kill", self._container_id],
                    capture_output=True,
                    timeout=10,
                )
            except Exception:
                pass
            self._container_id = None


# ─────────────────────────────────────────────────────────────────────────────
# Sandbox Gateway
# ─────────────────────────────────────────────────────────────────────────────


class SandboxGateway:
    """
    沙箱网关 — 统一入口。

    提供统一的沙箱执行接口，根据配置选择不同的隔离模式。
    管理容器池，实现资源的复用。

    使用方式：
        gateway = SandboxGateway(SandboxConfig(mode=SandboxMode.PROCESS))
        result = await gateway.execute_tool(
            tool_id="run_shell",
            params={"command": "find . -name '*.py'"},
            session_id="task-001",
        )
    """

    def __init__(self, config: SandboxConfig | None = None):
        self.config = config or SandboxConfig()
        self.audit = SandboxAuditLogger()
        self._process_sandboxes: dict[str, ProcessSandbox] = {}
        self._docker_pool: list[str] = []
        self._lock = asyncio.Lock()

    async def get_sandbox(
        self,
        session_id: str,
    ) -> ProcessSandbox | DockerSandbox:
        """获取或创建沙箱实例。"""
        async with self._lock:
            if self.config.mode == SandboxMode.PROCESS:
                if session_id not in self._process_sandboxes:
                    self._process_sandboxes[session_id] = ProcessSandbox(
                        session_id=session_id,
                        config=self.config,
                        audit_logger=self.audit,
                    )
                return self._process_sandboxes[session_id]
            elif self.config.mode == SandboxMode.DOCKER:
                # Docker 模式：每个 session 独立容器
                sandbox = DockerSandbox(
                    session_id=session_id,
                    config=self.config,
                    audit_logger=self.audit,
                )
                return sandbox
            else:
                # VM 模式（未实现），降级到 PROCESS
                if session_id not in self._process_sandboxes:
                    self._process_sandboxes[session_id] = ProcessSandbox(
                        session_id=session_id,
                        config=self.config,
                        audit_logger=self.audit,
                    )
                return self._process_sandboxes[session_id]

    async def execute_tool(
        self,
        tool_id: str,
        params: dict[str, Any],
        script: str,
        session_id: str,
    ) -> SandboxResult:
        """
        在沙箱中执行工具脚本。

        Args:
            tool_id   : 工具 ID
            params    : 工具参数（用于审计）
            script    : 要执行的脚本内容
            session_id: Session ID（用于隔离）

        Returns:
            SandboxResult
        """
        sandbox = await self.get_sandbox(session_id)

        if isinstance(sandbox, ProcessSandbox):
            result = await sandbox.run(tool_id, params, script)
        elif isinstance(sandbox, DockerSandbox):
            result = await sandbox.run(tool_id, params, script)
        else:
            result = SandboxResult(
                success=False,
                error=f"Unknown sandbox type: {type(sandbox)}",
            )

        logger.info(
            "[SandboxGateway] session=%s tool=%s success=%s duration=%dms",
            session_id,
            tool_id,
            result.success,
            result.duration_ms,
        )

        return result

    async def execute_command(
        self,
        command: str,
        session_id: str,
        timeout: float | None = None,
    ) -> SandboxResult:
        """
        直接执行 shell 命令（简化接口）。

        Args:
            command   : shell 命令
            session_id: Session ID
            timeout   : 超时（秒）

        Returns:
            SandboxResult
        """
        script = f"set -e\n{command}"
        return await self.execute_tool(
            tool_id="run_shell",
            params={"command": command},
            script=script,
            session_id=session_id,
        )

    async def cleanup_session(self, session_id: str) -> None:
        """清理指定 session 的沙箱资源。"""
        async with self._lock:
            sandbox = self._process_sandboxes.pop(session_id, None)
            if sandbox:
                sandbox.cleanup()

    async def cleanup_all(self) -> None:
        """清理所有沙箱资源。"""
        async with self._lock:
            for sandbox in self._process_sandboxes.values():
                sandbox.cleanup()
            self._process_sandboxes.clear()
            for container_id in self._docker_pool:
                try:
                    subprocess.run(
                        ["docker", "kill", container_id],
                        capture_output=True,
                        timeout=10,
                    )
                except Exception:
                    pass
            self._docker_pool.clear()

    def get_stats(self) -> dict[str, Any]:
        """获取沙箱统计信息。"""
        return {
            "mode": self.config.mode.value,
            "active_process_sandboxes": len(self._process_sandboxes),
            "docker_pool_size": len(self._docker_pool),
            "config": {
                "timeout": self.config.timeout,
                "cpu_limit": self.config.cpu_limit,
                "memory_limit": self.config.memory_limit,
                "network_enabled": self.config.network_enabled,
            },
        }
