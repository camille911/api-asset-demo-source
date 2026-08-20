"""Repository management MCP tools (register / scan entry points)."""
from __future__ import annotations

import datetime
from typing import Any

from pydantic import BaseModel

from git_asset_mcp.app import AppContext
from git_asset_mcp.security import redact_secrets


class RegisterResult(BaseModel):
    repo_id: str
    repository_url: str
    resolved_commit: str
    mirror_status: str


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def register_repository_tools(mcp: Any, ctx: AppContext) -> None:
    @mcp.tool()
    def repository_register(repository_url: str, ref: str = "main") -> RegisterResult:
        """Register a GitHub repository and create a local read-only mirror.

        Resolves ``ref`` to a full commit SHA. Never pushes or modifies the
        remote. The returned URL has any embedded credentials redacted.
        """
        result = ctx.provider.register(repository_url, ref)
        ctx.db.upsert_repository(
            repo_id=result.repo_id,
            repository_url=result.repository_url,
            default_ref=ref,
            now=_now(),
        )
        return RegisterResult(
            repo_id=result.repo_id,
            repository_url=redact_secrets(result.repository_url),
            resolved_commit=result.resolved_commit,
            mirror_status="ready",
        )
