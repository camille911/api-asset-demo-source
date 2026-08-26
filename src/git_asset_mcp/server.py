"""MCP server entry point.

Exposes the server as a module-level ``mcp`` so that:
- ``git-asset-mcp serve`` calls ``mcp.run(...)``;
- tests and the MCP Inspector can import it and connect in-memory via ``Client(mcp)``.
"""
from __future__ import annotations

import os

from mcp.server import MCPServer
from pydantic import BaseModel

from git_asset_mcp import __version__
from git_asset_mcp.app import AppContext
from git_asset_mcp.tools.package_tools import register_package_tools
from git_asset_mcp.tools.proposal_tools import register_proposal_tools
from git_asset_mcp.tools.rag_tools import register_rag_tools
from git_asset_mcp.tools.repository_tools import register_repository_tools
from git_asset_mcp.tools.scan_tools import register_scan_tools

mcp = MCPServer("git-asset-api")
ctx = AppContext.build()

# 消费模式（CONSUMER_MODE=1）：只暴露只读检索工具（asset_rag_search /
# asset_rag_status + ping/server_info），隐藏扫描/提案/打包等生产工具。
# 大模型侧只能"检索命中 → 拿 wheel 复用"，看不到仓库与打包流水线，
# 避免产生"把 API 现成打包"之类的多余行为。
_consumer = os.environ.get("CONSUMER_MODE", "").lower() in ("1", "true", "yes", "on")
if _consumer:
    register_rag_tools(mcp, ctx)
else:
    register_repository_tools(mcp, ctx)
    register_scan_tools(mcp, ctx)
    register_proposal_tools(mcp, ctx)
    register_package_tools(mcp, ctx)
    register_rag_tools(mcp, ctx)


class ServerInfo(BaseModel):
    name: str
    version: str


@mcp.tool()
def ping() -> str:
    """Return a simple health check response."""
    return "pong"


@mcp.tool()
def server_info() -> ServerInfo:
    """Return the component name and version."""
    return ServerInfo(name="git-asset-api", version=__version__)
