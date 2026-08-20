"""Module detection: group symbols into candidate business modules.

MVP order (task book 11.3): package boundary -> import relations -> call
relations. Only package-boundary grouping is deterministic; richer detection
uses LLM enrichment in a later stage.
"""
from __future__ import annotations

from collections import defaultdict

from git_asset_mcp.store.database import Database


def _package_of(path: str) -> str:
    p = path.replace("\\", "/")
    parts = p.split("/")
    if len(parts) > 1:
        return "/".join(parts[:-1])
    return "."


def detect_modules(db: Database, repo_id: str, commit: str) -> list[dict]:
    rows = db._conn.execute(
        """
        SELECT f.path, s.symbol_id, s.qualified_name, s.symbol_type, s.signature
        FROM symbols s
        JOIN files f ON s.file_id = f.file_id
        WHERE f.repo_id = ? AND f.commit_sha = ?
          AND s.symbol_type != 'module'
        """,
        (repo_id, commit),
    ).fetchall()

    packages: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        packages[_package_of(r["path"])].append(dict(r))

    modules: list[dict] = []
    for package, symbols in sorted(packages.items()):
        entries = [s for s in symbols if s["symbol_type"] in ("function", "method", "class")]
        modules.append(
            {
                "name": package.replace("/", "."),
                "paths": sorted({s["path"] for s in symbols}),
                "entry_symbols": [s["qualified_name"] for s in entries],
                "symbol_count": len(symbols),
            }
        )
    return modules
