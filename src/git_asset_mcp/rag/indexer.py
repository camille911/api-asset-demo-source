"""RAG indexer: extract -> chunk -> embed -> store.

Idempotent per (repo_id, commit): re-indexing replaces the previous chunks
for that commit. Called automatically after each successful scan.
"""
from __future__ import annotations

import datetime

from git_asset_mcp.rag.chunker import chunk_contract
from git_asset_mcp.rag.contract_extractor import extract_contracts
from git_asset_mcp.rag.embedder import Embedder
from git_asset_mcp.store.database import Database


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def index_repository(
    db: Database,
    repo_id: str,
    commit: str,
    embedder: Embedder | None = None,
    language: str | list[str] | None = None,
) -> dict:
    """Build (or rebuild) the RAG index for one scanned commit.

    ``language`` filters which source languages are indexed; ``None`` indexes
    all supported languages found in the scan. Returns statistics: contracts
    indexed, chunks stored.
    """
    embedder = embedder or Embedder()
    contracts = extract_contracts(db, repo_id, commit, language)

    # 幂等：先清掉该 commit 的旧索引（contracts + chunks 一起删），立即提交，
    # 避免与后续 INSERT 在同一事务内导致 UNIQUE 冲突判定失败。
    from git_asset_mcp.rag.retriever import clear_cache
    clear_cache()
    db._conn.execute(
        """
        DELETE FROM rag_chunks
        WHERE contract_id IN (SELECT contract_id FROM rag_contracts
                              WHERE repo_id = ? AND commit_sha = ?)
        """,
        (repo_id, commit),
    )
    db._conn.execute(
        "DELETE FROM rag_contracts WHERE repo_id = ? AND commit_sha = ?",
        (repo_id, commit),
    )
    db._conn.commit()

    chunk_rows: list[tuple[int, int, str, bytes]] = []
    for contract in contracts:
        # 同一符号 qname 可能跨文件重复（如 C++ 同名 namespace/函数）：
        # 已存在则整体跳过（首个契约已索引），否则插入并生成 chunks。
        # 若复用 contract_id 再生成 chunk，会撞 (contract_id, chunk_index) 唯一约束。
        row = db._conn.execute(
            """
            SELECT contract_id FROM rag_contracts
            WHERE repo_id = ? AND commit_sha = ? AND symbol_qname = ?
            """,
            (repo_id, commit, contract["symbol_qname"]),
        ).fetchone()
        if row is not None:
            continue
        cur = db._conn.execute(
            """
            INSERT INTO rag_contracts (
                repo_id, commit_sha, symbol_qname, symbol_type, module_name,
                api_name, path, signature, docstring, language, contract_json,
                artifact_id, wheel_path, contract_hash, indexed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                contract["repo_id"], contract["commit_sha"],
                contract["symbol_qname"], contract["symbol_type"],
                contract["module_name"], contract["api_name"], contract["path"],
                contract["signature"], contract["docstring"], contract["language"],
                contract["contract_json"], contract["artifact_id"],
                contract["wheel_path"], contract["contract_hash"],
                contract["indexed_at"],
            ),
        )
        contract_id = cur.lastrowid
        for idx, content in chunk_contract(contract):
            chunk_rows.append((contract_id, idx, content, b""))

    # 批量 embedding（一次调用，快）
    texts = [row[2] for row in chunk_rows]
    if texts:
        matrix = embedder.embed(texts)
        chunk_rows = [
            (cid, idx, content, matrix[i].tobytes())
            for i, (cid, idx, content, _) in enumerate(chunk_rows)
        ]

    db._conn.executemany(
        "INSERT INTO rag_chunks (contract_id, chunk_index, content, embedding) "
        "VALUES (?, ?, ?, ?)",
        chunk_rows,
    )
    db._conn.commit()

    return {
        "repo_id": repo_id,
        "commit": commit,
        "contracts_indexed": len(contracts),
        "chunks_stored": len(chunk_rows),
        "model": embedder.model_name,
    }
