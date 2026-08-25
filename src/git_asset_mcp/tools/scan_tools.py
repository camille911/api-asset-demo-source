"""Scan and module-listing MCP tools."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from git_asset_mcp.analyzers.python.inventory import scan_repository
from git_asset_mcp.analyzers.python.module_detector import detect_modules
from git_asset_mcp.app import AppContext
from git_asset_mcp.rag.indexer import index_repository


class ModuleSummary(BaseModel):
    name: str
    entry_symbols: list[str]
    symbol_count: int
    paths: list[str]


class ModuleListResult(BaseModel):
    modules: list[ModuleSummary]


class ScanResult(BaseModel):
    commit: str
    files_total: int
    files_ok: int
    files_failed: int
    symbols_total: int
    modules: list[ModuleSummary]
    rag_indexed: bool = False


def register_scan_tools(mcp: Any, ctx: AppContext) -> None:
    @mcp.tool()
    def repository_scan(repo_id: str, ref: str = "main") -> ScanResult:
        """Scan a repository at ``ref`` and persist symbols/modules to the store.

        Fetches the latest commit, resolves it to a full SHA, runs the Python
        AST analyzer, then detects candidate modules. Automatically builds
        the RAG semantic index for the scanned contracts. Returns a summary
        plus the module list. Requires ``repository_register`` first.
        """
        ctx.provider.fetch(repo_id)
        commit = ctx.provider.resolve_commit(repo_id, ref)
        scan = scan_repository(ctx.provider, ctx.db, repo_id, commit)
        modules = detect_modules(ctx.db, repo_id, commit)
        rag_indexed = False
        if scan["symbols_total"] > 0:
            try:
                index_repository(ctx.db, repo_id, commit)
                rag_indexed = True
            except Exception:
                # 语义索引失败不阻断扫描（embedding 模型缺失时降级）
                rag_indexed = False
        return ScanResult(
            commit=commit,
            files_total=scan["files_total"],
            files_ok=scan["files_ok"],
            files_failed=scan["files_failed"],
            symbols_total=scan["symbols_total"],
            modules=[ModuleSummary(**m) for m in modules],
            rag_indexed=rag_indexed,
        )

    @mcp.tool()
    def module_list(repo_id: str, commit: str = "") -> ModuleListResult:
        """List detected modules for a repository commit.

        Defaults to the most recently scanned commit for the repository.
        """
        target = commit or ctx.db.get_last_scanned_commit(repo_id)
        if not target:
            raise ValueError(f"repository {repo_id!r} has not been scanned yet")
        return ModuleListResult(
            modules=[ModuleSummary(**m) for m in detect_modules(ctx.db, repo_id, target)]
        )
