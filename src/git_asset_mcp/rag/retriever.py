"""Semantic retriever: query -> embedding -> cosine top-k with provenance.

Chunks are stored in SQLite with their float32 embeddings; retrieval loads
the matrix once per query (fine for local asset libraries of thousands of
chunks) and returns the top-k chunks joined with their full provenance:
chunk -> contract -> artifact/wheel/commit.
"""
from __future__ import annotations

import numpy as np

from git_asset_mcp.rag.embedder import Embedder, get_embedder
from git_asset_mcp.store.database import Database


def search(
    db: Database,
    query: str,
    top_k: int = 5,
    repo_id: str = "",
    embedder: Embedder | None = None,
) -> list[dict]:
    """Semantic search over the RAG index (query vectorization + retrieval).

    Returns up to ``top_k`` hits, each with the chunk content, similarity
    score and the full traceability chain (contract fields, wheel path,
    artifact id, commit, source file).
    """
    embedder = embedder or get_embedder()
    qvec = embedder.embed([query])[0]
    return search_vectors(db, qvec, top_k=top_k, repo_id=repo_id)


def search_vectors(
    db: Database,
    qvec: np.ndarray,
    top_k: int = 5,
    repo_id: str = "",
) -> list[dict]:
    """Retrieval only: cosine top-k against the stored chunk matrix.

    Takes an already-embedded query vector so callers can measure the pure
    retrieval cost without the (fixed) embedding inference overhead.
    """
    where = "WHERE ch.embedding IS NOT NULL"
    params: list = []
    if repo_id:
        where += " AND rc.repo_id = ?"
        params.append(repo_id)

    rows = db._conn.execute(
        f"""
        SELECT ch.chunk_id, ch.content, ch.embedding,
               rc.contract_id, rc.symbol_qname, rc.module_name, rc.api_name,
               rc.path, rc.signature, rc.docstring, rc.artifact_id,
               rc.wheel_path, rc.contract_hash, rc.repo_id, rc.commit_sha
        FROM rag_chunks ch
        JOIN rag_contracts rc ON ch.contract_id = rc.contract_id
        {where}
        """,
        params,
    ).fetchall()

    if not rows:
        return []

    matrix = np.vstack([np.frombuffer(r["embedding"], dtype=np.float32) for r in rows])
    scores = matrix @ qvec  # 已归一化 -> 点积即余弦

    order = np.argsort(-scores)[:top_k]
    hits: list[dict] = []
    for pos in order:
        r = rows[int(pos)]
        hits.append(
            {
                "score": round(float(scores[pos]), 4),
                "chunk_id": r["chunk_id"],
                "content": r["content"],
                "provenance": {
                    "contract_id": r["contract_id"],
                    "symbol": r["symbol_qname"],
                    "module": r["module_name"],
                    "api": r["api_name"],
                    "source_path": r["path"],
                    "signature": r["signature"],
                    "docstring": r["docstring"],
                    "artifact_id": r["artifact_id"],
                    "wheel_path": r["wheel_path"],
                    "contract_hash": r["contract_hash"],
                    "repo_id": r["repo_id"],
                    "commit": r["commit_sha"],
                },
            }
        )
    return hits


def index_stats(db: Database) -> dict:
    contracts = db._conn.execute("SELECT COUNT(*) FROM rag_contracts").fetchone()[0]
    chunks = db._conn.execute("SELECT COUNT(*) FROM rag_chunks").fetchone()[0]
    repos = db._conn.execute(
        "SELECT COUNT(DISTINCT repo_id) FROM rag_contracts"
    ).fetchone()[0]
    return {"contracts": contracts, "chunks": chunks, "repositories": repos}
