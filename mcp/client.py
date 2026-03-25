"""
MCP Client Module — 连接外部 MCP Servers

MCP（Model Context Protocol）让你的 AI Agent 能够连接外部工具和数据源。

当前支持的 MCP Servers：
    - github    : GitHub API 工具（查 star/PR/issue/contributors）
    - cve_db    : CVE 漏洞库查询
    - jira      : 项目管理工具（可选）

使用方式：
    from mcp.client import MCPClient

    client = MCPClient()
    await client.connect("github", server_command=["npx", "-y", "@modelcontextprotocol/server-github", "--token", "..."])
    await client.connect("cve_db", server_command=["python", "mcp/servers/cve_db_server.py"])

    # 在 SkillAgent 中使用
    mcp_tools = client.list_tools()
    # → [{"name": "get_repo_info", "description": "...", ...}]

    result = await client.call_tool("get_repo_info", {"owner": "owner", "repo": "repo"})
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# MCP Server Definition
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class MCPServerConfig:
    """MCP Server 配置。"""
    name: str                       # Server 标识
    command: list[str]              # 启动命令，如 ["npx", "-y", "@modelcontextprotocol/server-github"]
    env: dict[str, str] = field(default_factory=dict)   # 环境变量（如 API token）
    enabled: bool = True            # 是否启用


# ─────────────────────────────────────────────────────────────────────────────
# MCP Client（基于 stdio 通信）
# ─────────────────────────────────────────────────────────────────────────────

class MCPClient:
    """
    MCP Client — 通过 stdio 与 MCP Server 进程通信。

    MCP Server 通过 JSON-RPC over stdio 进行通信。
    客户端负责：
    1. 启动 Server 进程
    2. 发送 initialize 请求
    3. 列出可用工具（tools/list）
    4. 调用工具（tools/call）
    5. 优雅关闭
    """

    def __init__(self):
        self._servers: dict[str, subprocess.Popen] = {}
        self._tools: dict[str, list[dict[str, Any]]] = {}  # server_name → tools
        self._initialized: dict[str, bool] = {}

    # ── Server 管理 ────────────────────────────────────────────────────────

    async def connect(self, config: MCPServerConfig) -> None:
        """
        连接并启动一个 MCP Server。

        Args:
            config: Server 配置

        使用方式：
            config = MCPServerConfig(
                name="github",
                command=["npx", "-y", "@modelcontextprotocol/server-github"],
                env={"GITHUB_PERSONAL_ACCESS_TOKEN": "..."},
            )
            await client.connect(config)
        """
        if config.name in self._servers:
            logger.warning("[MCP] Server '%s' 已连接，跳过", config.name)
            return

        logger.info("[MCP] 启动 Server: %s — %s", config.name, " ".join(config.command))

        env = {**subprocess.os.environ, **config.env}

        try:
            proc = subprocess.Popen(
                config.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
            )
        except Exception as exc:
            logger.error("[MCP] 启动 Server '%s' 失败: %s", config.name, exc)
            return

        self._servers[config.name] = proc

        # 发送 initialize
        await self._send_request(config.name, "initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "clientInfo": {"name": "odin-mcp-client", "version": "0.2.0"},
        })

        # 初始化完成通知
        await self._send_notification(config.name, "notifications/initialized", {})

        # 列出工具
        await self._list_tools(config.name)

        self._initialized[config.name] = True
        logger.info("[MCP] Server '%s' 连接成功，已注册 %d 个工具",
                    config.name, len(self._tools.get(config.name, [])))

    async def disconnect(self, name: str) -> None:
        """断开并关闭 MCP Server。"""
        if name not in self._servers:
            return

        proc = self._servers[name]
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

        del self._servers[name]
        self._initialized.pop(name, None)
        self._tools.pop(name, None)
        logger.info("[MCP] Server '%s' 已断开", name)

    # ── 工具管理 ────────────────────────────────────────────────────────────

    def list_tools(self) -> list[dict[str, Any]]:
        """
        列出所有已连接 Server 的工具。

        返回格式与 ToolExecutor.list_tools() 兼容。
        """
        result = []
        for server_name, tools in self._tools.items():
            for tool in tools:
                result.append({
                    "name": tool.get("name", ""),
                    "description": tool.get("description", ""),
                    "input_schema": tool.get("inputSchema", {}),
                    "_mcp_server": server_name,
                })
        return result

    def list_tools_by_server(self, server_name: str) -> list[dict[str, Any]]:
        """列出特定 Server 的工具。"""
        return self._tools.get(server_name, [])

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """
        调用 MCP Server 上的工具。

        Args:
            tool_name : 工具名称（格式：server_name/tool_name）
            arguments : 工具参数

        Returns:
            {"success": True, "content": "..."} 或 {"success": False, "error": "..."}

        使用方式：
            result = await client.call_tool("github/get_repo_info", {"owner": "...", "repo": "..."})
        """
        if "/" in tool_name:
            server_name, actual_name = tool_name.split("/", 1)
        else:
            # 查找工具所在的 server
            server_name = self._find_server_for_tool(tool_name)
            actual_name = tool_name

        if not server_name:
            return {"success": False, "error": f"Tool '{tool_name}' not found in any connected server"}

        try:
            response = await self._send_request(server_name, "tools/call", {
                "name": actual_name,
                "arguments": arguments,
            })

            # 解析 MCP 工具响应
            content = response.get("content", [])
            if isinstance(content, list) and content:
                # MCP 返回 content: [{type: "text", text: "..."}]
                text_parts = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
                output = "\n".join(text_parts)
            else:
                output = json.dumps(content, ensure_ascii=False)

            return {
                "success": True,
                "output": output,
                "raw_response": response,
            }

        except Exception as exc:
            logger.exception("[MCP] 调用工具 '%s' 失败", tool_name)
            return {"success": False, "error": str(exc)}

    # ── 内部通信 ────────────────────────────────────────────────────────────

    async def _send_request(
        self,
        server_name: str,
        method: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        """发送 JSON-RPC 请求到 Server。"""
        proc = self._servers.get(server_name)
        if proc is None or proc.stdin is None or proc.stdout is None:
            raise RuntimeError(f"Server '{server_name}' not connected")

        request_id = f"req_{id(params)}"
        rpc_request = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }

        request_json = json.dumps(rpc_request) + "\n"
        proc.stdin.write(request_json)
        proc.stdin.flush()

        # 读取响应
        import select
        readable, _, _ = select.select([proc.stdout], [], [], 30)
        if not readable:
            raise TimeoutError(f"MCP Server '{server_name}' response timeout")

        response_line = proc.stdout.readline()
        if not response_line:
            raise RuntimeError(f"MCP Server '{server_name}' stdout closed")

        response = json.loads(response_line)

        if "error" in response:
            raise RuntimeError(f"MCP error: {response['error']}")

        return response.get("result", {})

    async def _send_notification(
        self,
        server_name: str,
        method: str,
        params: dict[str, Any],
    ) -> None:
        """发送 JSON-RPC 通知（无响应期望）。"""
        proc = self._servers.get(server_name)
        if proc is None or proc.stdin is None:
            return

        notification = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        proc.stdin.write(json.dumps(notification) + "\n")
        proc.stdin.flush()

    async def _list_tools(self, server_name: str) -> None:
        """列出 Server 的工具。"""
        try:
            response = await self._send_request(server_name, "tools/list", {})
            tools = response.get("tools", [])
            self._tools[server_name] = tools
        except Exception as exc:
            logger.warning("[MCP] 列出 Server '%s' 工具失败: %s", server_name, exc)
            self._tools[server_name] = []

    def _find_server_for_tool(self, tool_name: str) -> str | None:
        """查找指定工具所在的 Server。"""
        for server_name, tools in self._tools.items():
            for tool in tools:
                if tool.get("name") == tool_name:
                    return server_name
        return None


# ─────────────────────────────────────────────────────────────────────────────
# MCP Tool Adapter — 将 MCP 工具适配为 Odin 的 Tool 接口
# ─────────────────────────────────────────────────────────────────────────────

class MCPToolAdapter:
    """
    将 MCP 工具适配为 Odin 的 Tool 接口。

    使用方式：
        adapter = MCPToolAdapter(mcp_client)
        tools = adapter.get_tools()  # list[Tool]
        executor.register(tools)   # 注册到 ToolExecutor
    """

    def __init__(self, mcp_client: MCPClient, server_name: str):
        self.client = mcp_client
        self.server_name = server_name

    def get_tools(self) -> list["MCPToolWrapper"]:
        """获取该 Server 的所有工具，包装为 Tool 接口。"""
        tools = []
        for tool_def in self.client.list_tools_by_server(self.server_name):
            tools.append(MCPToolWrapper(self.client, tool_def, self.server_name))
        return tools


class MCPToolWrapper:
    """
    将单个 MCP 工具包装为 Odin 的 Tool 接口。
    """

    def __init__(self, client: MCPClient, tool_def: dict[str, Any], server_name: str):
        self._client = client
        self._tool_def = tool_def
        self._server_name = server_name
        self.name = f"{server_name}/{tool_def.get('name', '')}"
        self.description = tool_def.get("description", "")
        schema = tool_def.get("inputSchema", {})
        self.input_schema = schema if isinstance(schema, dict) else {}

    async def execute(self, args: dict[str, Any]) -> dict[str, Any]:
        """异步执行 MCP 工具。"""
        return await self._client.call_tool(self.name, args)

    def __call__(self, args: dict[str, Any]) -> dict[str, Any]:
        """同步执行包装（需要在 asyncio 事件循环中调用）。"""
        try:
            loop = asyncio.get_running_loop()
            import concurrent.futures
            future = loop.run_in_executor(None, asyncio.run, self.execute(args))
            return future.result()
        except RuntimeError:
            # 没有运行中的事件循环 → 创建新的
            return asyncio.run(self.execute(args))
