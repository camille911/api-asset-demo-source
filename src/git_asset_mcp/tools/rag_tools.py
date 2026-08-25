"""RAG search MCP tools: semantic retrieval over the asset contract index."""
from __future__ import annotations

import time
from typing import Any

from pydantic import BaseModel, Field

from git_asset_mcp.app import AppContext
from git_asset_mcp.rag.embedder import get_embedder
from git_asset_mcp.rag.retriever import index_stats, search_vectors


class RagHit(BaseModel):
    score: float
    chunk_id: int
    content: str
    provenance: dict = Field(default_factory=dict)


class RagSearchResult(BaseModel):
    query: str
    total_hits: int
    latency_ms: float = 0.0
    hits: list[RagHit]


class RagStatusResult(BaseModel):
    contracts: int
    chunks: int
    repositories: int
    model: str = ""


def register_rag_tools(mcp: Any, ctx: AppContext) -> None:
    @mcp.tool()
    def asset_rag_search(
        query: str,
        top_k: int = 5,
        repo_id: str = "",
    ) -> RagSearchResult:
        """Semantically search the local asset contract index (RAG).

        Query in natural language, e.g. "订单折扣计算 API" or "mask sensitive
        fields". Returns the top-k matching API contracts with full
        provenance: symbol, module, source path, and — when the API has
        already been packaged — the wheel path to reuse directly. Falls back
        gracefully when the index is empty.
        """
        # 口径：latency_ms 只统计纯检索（SQL + 矩阵点积），不含查询向量化。
        embedder = get_embedder()
        qvec = embedder.embed([query])[0]
        t0 = time.perf_counter()
        hits = search_vectors(ctx.db, qvec, top_k=top_k, repo_id=repo_id)
        latency_ms = round((time.perf_counter() - t0) * 1000, 1)
        return RagSearchResult(
            query=query,
            total_hits=len(hits),
            latency_ms=latency_ms,
            hits=[RagHit(**h) for h in hits],
        )

    @mcp.tool()
    def asset_rag_status() -> RagStatusResult:
        """Report RAG index statistics (contracts/chunks/repositories)."""
        stats = index_stats(ctx.db)
        return RagStatusResult(**stats)
