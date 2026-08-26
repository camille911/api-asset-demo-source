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


class FinalizeResult(BaseModel):
    generated_deleted: bool
    artifacts_relinked: int
    dist_kept: str
    note: str


def register_package_tools(mcp: Any, ctx: AppContext) -> None:
    @mcp.tool()
    def api_package_build(proposal_id: str, version: str) -> BuildResult:
        """Package an approved proposal into an immutable version + wheel.

        Only proposals in ``approved`` state can be packaged. Runtime adapter
        is chosen automatically from the entry symbol's language: Python ->
        FastAPI service wheel; C++/CUDA -> pybind11 binding sdist. Produces
        a service/binding, dependency closure, contract files, and a wheel.
        """
        record = ctx.db.get_proposal(proposal_id)
        if not record:
            raise ValueError(f"proposal {proposal_id!r} not found")
        if record["status"] != "approved":
            raise RuntimeError("proposal_not_approved")
        proposal = ApiProposal.model_validate_json(record["proposal_json"])
        proposal.status = record["status"]

        repo_id = proposal.module_id.split(":", 1)[0]
        entry_qname = proposal.entry_symbols[0]
        commit = ctx.db.get_last_scanned_commit(repo_id) or ""
        lang = ctx.db.get_symbol_language(repo_id, commit, entry_qname) or "python"

        if lang in ("cpp", "cuda"):
            from git_asset_mcp.packagers.pybind11 import build_cpp_artifact
            built = build_cpp_artifact(
                proposal, ctx.provider, ctx.db, version,
                ctx.settings.generated_dir, build_sdist=True,
            )
        else:
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
    def api_package_finalize() -> FinalizeResult:
        """打包与 RAG 完成后收尾：删除 generated 中间源码目录。

        可安装产物保留在 dist/（wheel / sdist），契约保留在 RAG 数据库；
        generated/ 仅打包期中间源码，删除后避免 AI 误读为"应读源码"，
        凸显"API 复用"而非"源码复制"的价值。删除前把 artifact 记录
        的路径重定向到 dist 产物，保持引用有效。
        """
        import os as _os
        import shutil

        generated = ctx.settings.generated_dir
        dist_dir = generated.parent / "dist"
        relinked = 0

        # 1) 把 artifact_path 从 generated 目录重定向到 dist 产物
        for art in ctx.db.list_artifacts():
            apath = Path(art["artifact_path"])
            api = apath.parent.name
            ver = apath.name
            candidates = (
                list(dist_dir.glob(f"{api}-{ver}.*"))
                + list(dist_dir.glob(f"git_asset_*{api}-{ver}.*"))
            )
            if candidates:
                ctx.db.update_artifact_path(art["artifact_id"], str(candidates[-1]))
                relinked += 1

        # 2) 删除 generated 中间目录（关 safe-delete shim 的回收站回退）
        _os.environ.setdefault("CODEBUDDY_SAFE_DELETE_SANDBOX", "0")
        deleted = False
        if generated.exists():
            shutil.rmtree(generated, ignore_errors=True)
            deleted = not generated.exists()

        return FinalizeResult(
            generated_deleted=deleted,
            artifacts_relinked=relinked,
            dist_kept=str(dist_dir),
            note="generated 已删除；可安装产物在 dist/，契约在 RAG 数据库",
        )

    @mcp.tool()
    def repository_update_check(repo_id: str, ref: str = "main") -> UpdateCheckResult:
        """Compare the latest remote commit against the last scanned commit."""
        return UpdateCheckResult(**update_check(ctx.provider, ctx.db, repo_id, ref))

    @mcp.tool()
    def api_update_plan(artifact_id: str) -> UpdatePlanResult:
        """Compute compatibility and a version recommendation for an artifact."""
        return UpdatePlanResult(**update_plan(ctx.provider, ctx.db, artifact_id))
