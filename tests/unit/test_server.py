"""Stage 1: minimal MCP server tool discovery tests."""
import pytest
from mcp import Client

from git_asset_mcp.server import mcp


@pytest.mark.anyio
async def test_tools_are_discoverable():
    async with Client(mcp) as client:
        result = await client.list_tools()
        names = {t.name for t in result.tools}
        assert {"ping", "server_info"} <= names


@pytest.mark.anyio
async def test_ping():
    async with Client(mcp) as client:
        result = await client.call_tool("ping", {})
        assert result.structured_content == {"result": "pong"}


@pytest.mark.anyio
async def test_server_info():
    async with Client(mcp) as client:
        result = await client.call_tool("server_info", {})
        data = result.structured_content
        assert data["name"] == "git-asset-api"
        assert data["version"] == "0.1.0"
