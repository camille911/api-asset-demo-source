"""Contract extraction for RAG indexing.

Pulls API-level contract records from the scanned symbol table and joins
them with built artifacts (wheel path / contract hash) for traceability.
One public function = one API contract (the granularity chosen for RAG).
"""
from __future__ import annotations

import datetime
import json
from pathlib import Path

from git_asset_mcp.store.database import Database


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _is_public(symbol_type: str, qualified_name: str) -> bool:
    leaf = qualified_name.split(".")[-1]
    return not leaf.startswith("_") and symbol_type in ("function", "method")


def _module_of(path: str) -> str:
    p = path.replace("\\", "/")
    parts = p.split("/")
    return "/".join(parts[:-1]) if len(parts) > 1 else "."


def _artifact_wheel_path(artifact: dict, api_name: str | None) -> str | None:
    """Derive the wheel path for a built artifact.

    wheel lives at ``<generated_dir 的父目录>/dist/<api_name>-<version>-py3-none-any.whl``;
    artifact_path is ``<generated_dir>/<api_name>/<version>`` so the dist dir
    is ``artifact_path.parents[2] / "dist"``.
    """
    ap = artifact.get("artifact_path")
    version = artifact.get("semantic_version")
    if not ap or not api_name or not version:
        return None
    dist_dir = Path(ap).parents[2] / "dist"
    wheel = dist_dir / f"{api_name}-{version}-py3-none-any.whl"
    return str(wheel)


def extract_contracts(
    db: Database, repo_id: str, commit: str, language: str = "python",
) -> list[dict]:
    """Extract API-level contracts from scanned symbols.

    Returns a list of contract dicts, each describing one public function
    together with provenance (source path, module, signature, docstring)
    and, when available, the built artifact (artifact_id, wheel_path,
    contract_hash) that covers this symbol.
    """
    rows = db._conn.execute(
        """
        SELECT s.qualified_name, s.symbol_type, s.signature, s.docstring, f.path
        FROM symbols s
        JOIN files f ON s.file_id = f.file_id
        WHERE f.repo_id = ? AND f.commit_sha = ?
          AND s.symbol_type != 'module'
        """,
        (repo_id, commit),
    ).fetchall()

    artifacts = db._conn.execute(
        "SELECT a.artifact_id, a.semantic_version, a.contract_hash, "
        "a.entry_symbols, a.artifact_path, p.proposal_json "
        "FROM artifacts a LEFT JOIN api_proposals p ON a.proposal_id = p.proposal_id"
    ).fetchall()
    artifact_by_symbol: dict[str, dict] = {}
    for a in artifacts:
        try:
            proposal = json.loads(a["proposal_json"] or "{}")
        except (ValueError, TypeError):
            proposal = {}
        api_name = proposal.get("api_name")
        # 只关联入口符号：proposer 把选中符号放 entry_symbols[0]
        entry_symbols = proposal.get("entry_symbols") or []
        entry = entry_symbols[0] if entry_symbols else ""
        if entry and entry not in artifact_by_symbol:
            artifact_by_symbol[entry] = dict(a) | {"api_name": api_name}

    contracts: list[dict] = []
    for r in rows:
        if not _is_public(r["symbol_type"], r["qualified_name"]):
            continue
        qname = r["qualified_name"]
        art = artifact_by_symbol.get(qname)
        contracts.append(
            {
                "repo_id": repo_id,
                "commit_sha": commit,
                "symbol_qname": qname,
                "symbol_type": r["symbol_type"],
                "module_name": _module_of(r["path"]),
                "api_name": qname.split(".")[-1],
                "path": r["path"],
                "signature": r["signature"] or "",
                "docstring": r["docstring"] or "",
                "language": language,
                "contract_json": None,
                "artifact_id": art["artifact_id"] if art else None,
                "wheel_path": _artifact_wheel_path(art, art.get("api_name")) if art else None,
                "contract_hash": art["contract_hash"] if art else None,
                "indexed_at": _now(),
            }
        )
    return contracts
