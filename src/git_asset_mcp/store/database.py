"""SQLite metadata store with schema versioning.

All CREATE TABLE statements live here (no schema scattered in app code).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS repositories (
    repo_id TEXT PRIMARY KEY,
    repository_url TEXT NOT NULL,
    default_ref TEXT NOT NULL,
    last_scanned_commit TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scans (
    scan_id TEXT PRIMARY KEY,
    repo_id TEXT NOT NULL,
    scan_type TEXT NOT NULL,
    base_commit TEXT,
    target_commit TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    error_message TEXT
);

CREATE TABLE IF NOT EXISTS files (
    file_id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id TEXT NOT NULL,
    commit_sha TEXT NOT NULL,
    path TEXT NOT NULL,
    blob_sha TEXT,
    language TEXT,
    size_bytes INTEGER,
    is_test INTEGER NOT NULL DEFAULT 0,
    is_generated INTEGER NOT NULL DEFAULT 0,
    parse_status TEXT,
    UNIQUE (repo_id, commit_sha, path)
);

CREATE TABLE IF NOT EXISTS symbols (
    symbol_id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id INTEGER NOT NULL,
    qualified_name TEXT NOT NULL,
    symbol_type TEXT NOT NULL,
    signature TEXT,
    start_line INTEGER,
    end_line INTEGER,
    docstring TEXT,
    visibility TEXT
);

CREATE TABLE IF NOT EXISTS relations (
    relation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_symbol_id INTEGER NOT NULL,
    target_symbol_id INTEGER,
    target_name TEXT,
    relation_type TEXT NOT NULL,
    confidence TEXT,
    evidence TEXT
);

CREATE TABLE IF NOT EXISTS modules (
    module_id TEXT PRIMARY KEY,
    repo_id TEXT NOT NULL,
    commit_sha TEXT NOT NULL,
    name TEXT NOT NULL,
    summary TEXT,
    confidence TEXT,
    evidence_json TEXT
);

CREATE TABLE IF NOT EXISTS module_members (
    module_id TEXT NOT NULL,
    symbol_id INTEGER NOT NULL,
    member_role TEXT,
    PRIMARY KEY (module_id, symbol_id)
);

CREATE TABLE IF NOT EXISTS api_proposals (
    proposal_id TEXT PRIMARY KEY,
    module_id TEXT NOT NULL,
    proposal_json TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id TEXT PRIMARY KEY,
    proposal_id TEXT NOT NULL,
    semantic_version TEXT NOT NULL,
    source_commit TEXT NOT NULL,
    contract_hash TEXT,
    implementation_hash TEXT,
    artifact_path TEXT,
    verification_status TEXT,
    created_at TEXT NOT NULL,
    entry_symbols TEXT
);
"""


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False: MCP tools may run on a different thread than
        # the one that built the connection (asyncio event-loop thread).
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- repositories -------------------------------------------------------

    def upsert_repository(self, repo_id: str, repository_url: str, default_ref: str, now: str) -> None:
        self._conn.execute(
            """
            INSERT INTO repositories (repo_id, repository_url, default_ref, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(repo_id) DO UPDATE SET
                repository_url = excluded.repository_url,
                default_ref = excluded.default_ref,
                updated_at = excluded.updated_at
            """,
            (repo_id, repository_url, default_ref, now, now),
        )
        self._conn.commit()

    def set_last_scanned_commit(self, repo_id: str, commit: str, now: str) -> None:
        self._conn.execute(
            """
            INSERT INTO repositories (repo_id, repository_url, default_ref, last_scanned_commit, created_at, updated_at)
            VALUES (?, '', 'main', ?, ?, ?)
            ON CONFLICT(repo_id) DO UPDATE SET
                last_scanned_commit = excluded.last_scanned_commit,
                updated_at = excluded.updated_at
            """,
            (repo_id, commit, now, now),
        )
        self._conn.commit()

    def get_last_scanned_commit(self, repo_id: str) -> str | None:
        row = self._conn.execute(
            "SELECT last_scanned_commit FROM repositories WHERE repo_id = ?", (repo_id,)
        ).fetchone()
        return row["last_scanned_commit"] if row else None

    # -- files / symbols / relations ---------------------------------------

    def insert_file(
        self,
        repo_id: str,
        commit_sha: str,
        path: str,
        blob_sha: str | None,
        language: str | None,
        size_bytes: int | None,
        is_test: bool,
        parse_status: str | None,
    ) -> int:
        cur = self._conn.execute(
            """
            INSERT INTO files (repo_id, commit_sha, path, blob_sha, language, size_bytes,
                               is_test, is_generated, parse_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)
            ON CONFLICT(repo_id, commit_sha, path) DO UPDATE SET
                blob_sha = excluded.blob_sha,
                parse_status = excluded.parse_status
            """,
            (repo_id, commit_sha, path, blob_sha, language, size_bytes, int(is_test), parse_status),
        )
        self._conn.commit()
        return cur.lastrowid

    def insert_symbol(
        self,
        file_id: int,
        qualified_name: str,
        symbol_type: str,
        signature: str | None,
        start_line: int | None,
        end_line: int | None,
        docstring: str | None,
    ) -> int:
        cur = self._conn.execute(
            """
            INSERT INTO symbols (file_id, qualified_name, symbol_type, signature,
                                 start_line, end_line, docstring)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (file_id, qualified_name, symbol_type, signature, start_line, end_line, docstring),
        )
        self._conn.commit()
        return cur.lastrowid

    def insert_relation(
        self,
        source_symbol_id: int,
        target_name: str,
        relation_type: str,
        target_symbol_id: int | None = None,
        confidence: str | None = None,
        evidence: str | None = None,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO relations (source_symbol_id, target_symbol_id, target_name,
                                   relation_type, confidence, evidence)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (source_symbol_id, target_symbol_id, target_name, relation_type, confidence, evidence),
        )
        self._conn.commit()

    def count_symbols(self, repo_id: str, commit_sha: str) -> int:
        row = self._conn.execute(
            """
            SELECT COUNT(*) AS n FROM symbols s
            JOIN files f ON s.file_id = f.file_id
            WHERE f.repo_id = ? AND f.commit_sha = ?
            """,
            (repo_id, commit_sha),
        ).fetchone()
        return row["n"]

    # -- proposals ----------------------------------------------------------

    def insert_proposal(self, proposal_id: str, module_id: str, proposal_json: str, status: str, created_at: str) -> None:
        self._conn.execute(
            """
            INSERT INTO api_proposals (proposal_id, module_id, proposal_json, status, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (proposal_id, module_id, proposal_json, status, created_at),
        )
        self._conn.commit()

    def get_proposal(self, proposal_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM api_proposals WHERE proposal_id = ?", (proposal_id,)
        ).fetchone()
        return dict(row) if row else None

    def update_proposal_status(self, proposal_id: str, status: str) -> None:
        self._conn.execute(
            "UPDATE api_proposals SET status = ? WHERE proposal_id = ?",
            (status, proposal_id),
        )
        self._conn.commit()

    # -- artifacts ----------------------------------------------------------

    def insert_artifact(
        self,
        artifact_id: str,
        proposal_id: str,
        semantic_version: str,
        source_commit: str,
        contract_hash: str,
        implementation_hash: str,
        artifact_path: str,
        verification_status: str,
        created_at: str,
        entry_symbols: list[str],
    ) -> None:
        import json

        self._conn.execute(
            """
            INSERT INTO artifacts (artifact_id, proposal_id, semantic_version, source_commit,
                                   contract_hash, implementation_hash, artifact_path,
                                   verification_status, created_at, entry_symbols)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                artifact_id,
                proposal_id,
                semantic_version,
                source_commit,
                contract_hash,
                implementation_hash,
                artifact_path,
                verification_status,
                created_at,
                json.dumps(entry_symbols),
            ),
        )
        self._conn.commit()

    def find_artifact_by_symbols(self, entry_symbols: list[str]) -> dict | None:
        import json

        rows = self._conn.execute(
            """
            SELECT a.artifact_id, a.entry_symbols, p.module_id
            FROM artifacts a
            JOIN api_proposals p ON a.proposal_id = p.proposal_id
            WHERE a.entry_symbols IS NOT NULL
            """
        ).fetchall()
        target = set(entry_symbols)
        for r in rows:
            try:
                existing = set(json.loads(r["entry_symbols"]))
            except (json.JSONDecodeError, TypeError):
                continue
            if target & existing:
                return {"artifact_id": r["artifact_id"], "module_id": r["module_id"]}
        return None

    def blob_shas_for_paths(self, repo_id: str, commit_sha: str, paths: list[str]) -> list[str]:
        if not paths:
            return []
        placeholders = ",".join("?" for _ in paths)
        rows = self._conn.execute(
            f"""
            SELECT blob_sha FROM files
            WHERE repo_id = ? AND commit_sha = ? AND path IN ({placeholders})
            """,
            (repo_id, commit_sha, *paths),
        ).fetchall()
        return [r["blob_sha"] for r in rows if r["blob_sha"]]

    def get_artifact(self, artifact_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM artifacts WHERE artifact_id = ?", (artifact_id,)
        ).fetchone()
        return dict(row) if row else None

    def source_paths_for_symbols(self, repo_id: str, symbols: set[str]) -> set[str]:
        if not symbols:
            return set()
        placeholders = ",".join("?" for _ in symbols)
        rows = self._conn.execute(
            f"""
            SELECT DISTINCT f.path FROM files f
            JOIN symbols s ON s.file_id = f.file_id
            WHERE f.repo_id = ? AND s.qualified_name IN ({placeholders})
            """,
            (repo_id, *sorted(symbols)),
        ).fetchall()
        return {r["path"] for r in rows}

    def get_symbol_signature(self, repo_id: str, commit_sha: str, qualified_name: str) -> str | None:
        row = self._conn.execute(
            """
            SELECT s.signature FROM symbols s
            JOIN files f ON s.file_id = f.file_id
            WHERE f.repo_id = ? AND f.commit_sha = ? AND s.qualified_name = ?
            """,
            (repo_id, commit_sha, qualified_name),
        ).fetchone()
        return row["signature"] if row else None
