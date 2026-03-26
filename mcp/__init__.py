"""
MCP Module — 连接外部 MCP Servers

基于 Model Context Protocol，连接 GitHub、CVE 库等外部工具。
"""

from mcp.client import MCPClient, MCPServerConfig, MCPToolAdapter

__all__ = ["MCPClient", "MCPServerConfig", "MCPToolAdapter"]
