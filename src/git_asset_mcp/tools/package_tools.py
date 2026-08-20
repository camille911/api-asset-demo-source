"""Packaging, verification, and incremental-update MCP tools."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel

from git_asset_mcp.app import AppContext
from git_asset_mcp.packagers.fastapi.generator import build_artifact
from git_asset_mcp.packagers.fastapi.verifier import verify_artifact
from git_asset_mcp.proposal.schemas import ApiProposal
from git_asset_mcp.updater import update_check, update_plan


class BuildResult(BaseModel):
    artifact_id: str
    artifact_path: str
    wheel_path: str
    source_commit: str
    version: str
    contract_hash: str
    implementation_hash: str
    build_status: str


class CheckItem(BaseModel):
    name: str
    ok: bool
    detail: str | None = None


class VerifyResult(BaseModel):
    status: str
    checks: list[CheckItem]


class UpdateCheckResult(BaseModel):
    old_commit: str | None
    new_commit: str
    changed_files: list[str]
    added_files: list[str]
    deleted_files: list[str]
    renamed_files: list[str]
    affected_modules: list[str]
    affected_artifacts: list[str]
    has_changes: bool


class UpdatePlanResult(BaseModel):
    artifact_id: str
    change_summary: str
    implementation_changed: bool
    contract_changed: bool
    contract_hash_old: str | None
    contract_hash_new: str | None
    compatibility: str
    recommended_version: str
    allow_auto_rebuild: bool
    tests_needed: list[str]


def register_package_tools(mcp: Any, ctx: AppContext) -> None:
    @mcp.tool()
    def api_package_build(proposal_id: str, version: str) -> BuildResult:
        """Package an approved proposal into an immutable version + wheel.

        Only proposals in ``approved`` state can be packaged. Produces a
        FastAPI service, dependency closure, contract files, and a wheel.
        """
        record = ctx.db.get_proposal(proposal_id)
        if not record:
            raise ValueError(f"proposal {proposal_id!r} not found")
        if record["status"] != "approved":
            raise RuntimeError("proposal_not_approved")
        proposal = ApiProposal.model_validate_json(record["proposal_json"])
        proposal.status = record["status"]
        built = build_artifact(
            proposal, ctx.provider, ctx.db, version,
            ctx.settings.generated_dir, build_wheel=True,
        )
        return BuildResult(**built)

    @mcp.tool()
    def api_package_verify(artifact_id: str) -> VerifyResult:
        """Verify a built artifact: import, health, metadata, openapi."""
        record = ctx.db.get_artifact(artifact_id)
        if not record:
            raise ValueError(f"artifact {artifact_id!r} not found")
        verified = verify_artifact(Path(record["artifact_path"]))
        return VerifyResult(
            status=verified["status"],
            checks=[CheckItem(**c) for c in verified["checks"]],
        )

    @mcp.tool()
    def repository_update_check(repo_id: str, ref: str = "main") -> UpdateCheckResult:
        """Compare the latest remote commit against the last scanned commit."""
        return UpdateCheckResult(**update_check(ctx.provider, ctx.db, repo_id, ref))

    @mcp.tool()
    def api_update_plan(artifact_id: str) -> UpdatePlanResult:
        """Compute compatibility and a version recommendation for an artifact."""
        return UpdatePlanResult(**update_plan(ctx.provider, ctx.db, artifact_id))
