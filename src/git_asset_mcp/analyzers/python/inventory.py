"""Repository scan orchestration: files -> analyzer -> SQLite.

Language-agnostic: files are classified by name (see
``git_asset_mcp.analyzers.classify_file``) and dispatched to the matching
analyzer, so a single scan covers Python, C/C++/CUDA and Dockerfiles.
"""
from __future__ import annotations

import datetime
import uuid

from git_asset_mcp.analyzers import analyze_source, classify_file
from git_asset_mcp.analyzers.models import ParseError
from git_asset_mcp.providers.base import RepositoryProvider
from git_asset_mcp.store.database import Database


def is_test_file(path: str) -> bool:
    p = path.lower().replace("\\", "/")
    return (
        p.startswith("test_")
        or p.endswith("_test.py")
        or "/test_" in p
        or "/tests/" in p
        or p.startswith("tests/")
    )


def is_generated_file(path: str) -> bool:
    p = path.lower().replace("\\", "/")
    return any(marker in p for marker in ("__pycache__", ".egg-info", "/build/", "/dist/"))


def scan_repository(
    provider: RepositoryProvider,
    db: Database,
    repo_id: str,
    commit: str,
    scan_type: str = "full",
    max_file_bytes: int = 2 * 1024 * 1024,
) -> dict:
    """Scan ``commit`` of ``repo_id`` and persist files/symbols/relations."""
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    scan_id = str(uuid.uuid4())

    entries = provider.ls_tree(repo_id, commit)
    supported = [
        (path, sha, lang)
        for path, sha in entries
        if (lang := classify_file(path)) is not None
    ]

    files_ok = 0
    files_failed = 0
    symbols_total = 0

    for path, blob_sha, language in supported:
        if is_test_file(path) or is_generated_file(path):
            continue
        source = provider.read_blob(repo_id, blob_sha)
        size = len(source.encode("utf-8", "ignore"))
        if size > max_file_bytes:
            continue

        file_id = db.insert_file(
            repo_id=repo_id,
            commit_sha=commit,
            path=path,
            blob_sha=blob_sha,
            language=language,
            size_bytes=size,
            is_test=False,
            parse_status="pending",
        )

        try:
            analysis = analyze_source(source, path)
        except ParseError:
            db.insert_file(
                repo_id=repo_id, commit_sha=commit, path=path, blob_sha=blob_sha,
                language=language, size_bytes=size, is_test=False, parse_status="failed",
            )
            files_failed += 1
            continue

        symbol_ids: dict[str, int] = {}
        for sym in analysis.symbols:
            sid = db.insert_symbol(
                file_id=file_id,
                qualified_name=sym.qualified_name,
                symbol_type=sym.symbol_type,
                signature=sym.signature,
                start_line=sym.start_line,
                end_line=sym.end_line,
                docstring=sym.docstring,
            )
            symbol_ids[sym.qualified_name] = sid
            symbols_total += 1

        module_qname = analysis.symbols[0].qualified_name if analysis.symbols else path
        module_symbol_id = symbol_ids.get(module_qname, 0)
        for imp in analysis.imports:
            db.insert_relation(
                source_symbol_id=module_symbol_id,
                target_name=imp.module,
                relation_type="imports",
            )
        for call in analysis.calls:
            db.insert_relation(
                source_symbol_id=symbol_ids.get(call.caller, module_symbol_id),
                target_name=call.callee,
                relation_type="calls",
                confidence="inferred" if call.inferred else None,
            )

        db.insert_file(
            repo_id=repo_id, commit_sha=commit, path=path, blob_sha=blob_sha,
            language=language, size_bytes=size, is_test=False, parse_status="ok",
        )
        files_ok += 1

    db.set_last_scanned_commit(repo_id, commit, now)

    return {
        "scan_id": scan_id,
        "repo_id": repo_id,
        "commit": commit,
        "files_total": len(supported),
        "files_ok": files_ok,
        "files_failed": files_failed,
        "symbols_total": symbols_total,
    }
