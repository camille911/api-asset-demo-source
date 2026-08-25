"""Incremental update: detect changes and plan version bumps (task book 13-14)."""
from __future__ import annotations

from git_asset_mcp.analyzers import classify_file
from git_asset_mcp.packagers.fastapi.contract import contract_hash
from git_asset_mcp.packagers.fastapi.generator import _minimal_openapi
from git_asset_mcp.proposal.proposer import propose_api
from git_asset_mcp.providers.base import RepositoryProvider
from git_asset_mcp.store.database import Database


def update_check(provider: RepositoryProvider, db: Database, repo_id: str, ref: str = "main") -> dict:
    """Compare latest remote commit against last scanned commit."""
    provider.fetch(repo_id)
    new_commit = provider.resolve_commit(repo_id, ref)
    old_commit = db.get_last_scanned_commit(repo_id)

    if not old_commit or old_commit == new_commit:
        return {
            "old_commit": old_commit,
            "new_commit": new_commit,
            "changed_files": [],
            "added_files": [],
            "deleted_files": [],
            "renamed_files": [],
            "affected_modules": [],
            "affected_artifacts": [],
            "has_changes": False,
        }

    changes = provider.diff(repo_id, old_commit, new_commit)
    by_status = {"added": [], "modified": [], "deleted": [], "renamed": []}
    for c in changes:
        by_status.setdefault(c.status, []).append(c.path)

    affected_modules = sorted({_module_of(c.path) for c in changes if classify_file(c.path)})
    affected_artifacts = _affected_artifacts(db, repo_id, [c.path for c in changes])

    return {
        "old_commit": old_commit,
        "new_commit": new_commit,
        "changed_files": by_status["modified"],
        "added_files": by_status["added"],
        "deleted_files": by_status["deleted"],
        "renamed_files": by_status["renamed"],
        "affected_modules": affected_modules,
        "affected_artifacts": affected_artifacts,
        "has_changes": True,
    }


def update_plan(provider: RepositoryProvider, db: Database, artifact_id: str) -> dict:
    """Compute compatibility and a version recommendation for an artifact."""
    rec = db.get_artifact(artifact_id)
    if not rec:
        raise ValueError(f"artifact {artifact_id!r} not found")

    proposal = _proposal_from_record(db, rec["proposal_id"])
    module_name = proposal.module_id.split(":", 1)[-1]
    repo_id = proposal.module_id.split(":", 1)[0]
    new_commit = db.get_last_scanned_commit(repo_id)

    # 重新提案（新 commit）并计算新契约 hash。
    new_proposal = propose_api(db, repo_id, new_commit, module_name, target_capability=proposal.api_name)
    new_chash = contract_hash(_minimal_openapi(new_proposal, new_proposal.api_name, rec["semantic_version"]))

    contract_changed = rec["contract_hash"] != new_chash
    implementation_changed = _implementation_changed(db, repo_id, rec["source_commit"], new_commit or "", proposal.source_paths)

    if not contract_changed and not implementation_changed:
        compatibility, recommendation, auto = "compatible", "none", False
    elif not contract_changed and implementation_changed:
        compatibility, recommendation, auto = "compatible", "patch", True
    elif contract_changed:
        compatibility, recommendation, auto = "breaking", "major", False
    else:
        compatibility, recommendation, auto = "unknown", "manual", False

    return {
        "artifact_id": artifact_id,
        "change_summary": f"contract={'changed' if contract_changed else 'unchanged'}, "
        f"implementation={'changed' if implementation_changed else 'unchanged'}",
        "implementation_changed": implementation_changed,
        "contract_changed": contract_changed,
        "contract_hash_old": rec["contract_hash"],
        "contract_hash_new": new_chash,
        "compatibility": compatibility,
        "recommended_version": recommendation,
        "allow_auto_rebuild": auto,
        "tests_needed": ["contract", "golden", "health"],
    }


def _module_of(path: str) -> str:
    p = path.replace("\\", "/")
    parts = p.split("/")
    return "/".join(parts[:-1]) if len(parts) > 1 else "."


def _affected_artifacts(db: Database, repo_id: str, changed_paths: set[str]) -> list[str]:
    rows = db._conn.execute(
        "SELECT artifact_id, entry_symbols FROM artifacts WHERE entry_symbols IS NOT NULL"
    ).fetchall()
    affected = []
    for r in rows:
        symbols = _parse_symbols(r["entry_symbols"])
        paths = db.source_paths_for_symbols(repo_id, symbols)
        if paths & set(changed_paths):
            affected.append(r["artifact_id"])
    return affected


def _parse_symbols(raw: str | None) -> set[str]:
    import json

    try:
        return set(json.loads(raw or "[]"))
    except (json.JSONDecodeError, TypeError):
        return set()


def _proposal_from_record(db: Database, proposal_id: str):
    from git_asset_mcp.proposal.schemas import ApiProposal

    rec = db.get_proposal(proposal_id)
    return ApiProposal.model_validate_json(rec["proposal_json"])


def _implementation_changed(db: Database, repo_id: str, old_commit: str, new_commit: str, source_paths: list[str]) -> bool:
    if not old_commit or not new_commit:
        return False
    old_blobs = set(db.blob_shas_for_paths(repo_id, old_commit, source_paths))
    new_blobs = set(db.blob_shas_for_paths(repo_id, new_commit, source_paths))
    return old_blobs != new_blobs
