"""MCP server entry point.

Exposes the server as a module-level ``mcp`` so that:
- ``git-asset-mcp serve`` calls ``mcp.run(...)``;
- tests and the MCP Inspector can import it and connect in-memory via ``Client(mcp)``.
"""
from __future__ import annotations

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
