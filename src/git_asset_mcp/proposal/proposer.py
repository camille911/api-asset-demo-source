"""Deterministic API proposal generator (works with LLM disabled)."""
from __future__ import annotations

import re
import uuid

from git_asset_mcp.proposal.schemas import ApiProposal
from git_asset_mcp.store.database import Database


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _public_entry(symbol_type: str, qualified_name: str) -> bool:
    leaf = qualified_name.split(".")[-1]
    return not leaf.startswith("_") and symbol_type in ("function", "method")


def _select_entry(entries: list[dict], entry_symbol: str) -> dict:
    """Pick the target entry by exact or suffix symbol match.

    Defaults to ``entries[0]`` (first public function) when no symbol is
    given, preserving the legacy behaviour. Raises when an explicit symbol
    cannot be matched.
    """
    if not entry_symbol:
        return entries[0]
    for e in entries:
        qname = e["qualified_name"]
        if qname == entry_symbol or qname.endswith("." + entry_symbol):
            return e
    raise ValueError(f"entry_symbol {entry_symbol!r} not found in module")


def _params_from_signature(signature: str) -> list[str]:
    m = re.search(r"\((.*)\)", signature)
    if not m:
        return []
    return [p.strip() for p in m.group(1).split(",") if p.strip() and p.strip() != "self"]


def _request_schema(signature: str) -> dict:
    params = _params_from_signature(signature)
    properties = {p.split(":")[0].strip(): {"type": "string"} for p in params if p.split(":")[0].strip()}
    return {"type": "object", "properties": properties, "required": list(properties.keys())}


def _response_schema(signature: str) -> dict:
    m = re.search(r"->\s*(\S+)", signature)
    ret = m.group(1) if m else "None"
    if ret in ("None", "NoneType"):
        return {"type": "object", "properties": {"valid": {"type": "boolean"}}}
    return {"type": "string", "description": f"returns {ret}"}


def propose_api(
    db: Database,
    repo_id: str,
    commit: str,
    module_name: str,
    target_capability: str = "",
    entry_symbol: str = "",
) -> ApiProposal:
    """Generate a deterministic API proposal for a module's public entry points.

    ``entry_symbol`` optionally selects which public function becomes the API
    entry (exact or leaf-suffix match). Without it, the first public function
    is used (legacy behaviour). The selected entry is placed first in
    ``entry_symbols`` so packaging targets it.
    """
    rows = db._conn.execute(
        """
        SELECT s.qualified_name, s.symbol_type, s.signature, f.path
        FROM symbols s
        JOIN files f ON s.file_id = f.file_id
        WHERE f.repo_id = ? AND f.commit_sha = ?
          AND s.symbol_type != 'module'
        """,
        (repo_id, commit),
    ).fetchall()

    entries = [
        dict(r) for r in rows
        if _public_entry(r["symbol_type"], r["qualified_name"])
        and module_name in r["qualified_name"]
    ]
    if not entries:
        raise ValueError(f"no public entry symbols found for module {module_name!r}")

    entry = _select_entry(entries, entry_symbol)
    ordered = [entry] + [e for e in entries if e is not entry]
    leaf = entry["qualified_name"].split(".")[-1]
    api_name = target_capability or leaf
    capability = target_capability or leaf

    return ApiProposal(
        proposal_id=str(uuid.uuid4()),
        module_id=f"{repo_id}:{module_name}",
        api_name=api_name,
        capability=capability,
        path=f"/v1/{_slugify(module_name)}/{_slugify(capability)}",
        request_schema=_request_schema(entry["signature"]),
        response_schema=_response_schema(entry["signature"]),
        error_model={"error": {"type": "object", "properties": {"detail": {"type": "string"}}}},
        source_paths=sorted({r["path"] for r in entries}),
        entry_symbols=[e["qualified_name"] for e in ordered],
        adapter_needed=True,
        risks=[
            "deterministic proposal: field-level schema is coarse and requires confirmation or LLM enrichment"
        ],
        confidence="low",
        status="proposed",
        requires_confirmation=True,
    )
