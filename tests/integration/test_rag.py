"""Integration test: RAG contract extraction, chunking, indexing, retrieval."""
import subprocess

import numpy as np
import pytest

from git_asset_mcp.analyzers.python.inventory import scan_repository
from git_asset_mcp.proposal.proposer import propose_api
from git_asset_mcp.providers.github import GithubProvider
from git_asset_mcp.rag.chunker import chunk_contract
from git_asset_mcp.rag.contract_extractor import extract_contracts
from git_asset_mcp.rag.indexer import index_repository
from git_asset_mcp.rag.retriever import index_stats, search
from git_asset_mcp.store.database import Database

MULTI_SRC = {
    "customer_id.py": (
        '"""Customer identifier normalization."""\n'
        "import re\n"
        "\n"
        "def normalize_customer_id(value: str) -> str:\n"
        '    """Normalize a customer id, strip separators and uppercase."""\n'
        '    return re.sub(r"[\\s-]+", "", value).upper()\n'
    ),
    "masking.py": (
        '"""Sensitive field masking."""\n'
        "from copy import deepcopy\n"
        "\n"
        "def mask_sensitive_fields(payload: dict, fields: set) -> dict:\n"
        '    """Return a deep copy with selected top-level values replaced by `***`."""\n'
        "    return deepcopy(payload)\n"
    ),
    "request_signer.py": (
        '"""Request signing."""\n'
        "import hashlib, hmac\n"
        "\n"
        "def sign_request(payload: dict, secret: str) -> str:\n"
        '    """Return the HMAC-SHA256 digest for a canonical request."""\n'
        '    return "sig"\n'
    ),
}


class MockEmbedder:
    """Deterministic fake embedder: character-bigram hashing -> 128-dim vector.

    Character n-grams capture identifier-level semantics (snake_case names,
    docstring keywords) far better than word bags, so the mock behaves like
    a real embedding model for local retrieval tests.
    """

    def __init__(self):
        self.model_name = "mock"

    @staticmethod
    def _bigrams(text: str) -> list[str]:
        norm = "".join(ch if ch.isalnum() else "_" for ch in text.lower())
        return [norm[i:i + 2] for i in range(len(norm) - 1)] or [norm]

    def embed(self, texts):
        vecs = []
        for text in texts:
            v = np.zeros(128, dtype=np.float32)
            for gram in set(self._bigrams(text)):
                v[hash(gram) % 128] += 1.0
            norm = np.linalg.norm(v) or 1.0
            vecs.append(v / norm)
        return np.asarray(vecs, dtype=np.float32)


def _git(args, cwd):
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)


@pytest.fixture
def scanned(tmp_path):
    repo = tmp_path / "source"
    repo.mkdir()
    _git(["init", "-q", "-b", "main"], repo)
    _git(["config", "user.email", "t@e.com"], repo)
    _git(["config", "user.name", "t"], repo)
    pkg = repo / "company_shared_api"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    for name, src in MULTI_SRC.items():
        (pkg / name).write_text(src)
    _git(["add", "."], repo)
    _git(["commit", "-q", "-m", "init"], repo)

    provider = GithubProvider(data_dir=tmp_path / "data", allow_local_paths=True)
    ref = provider.register(str(repo), "main")
    db = Database(tmp_path / "data" / "scan.db")
    scan_repository(provider, db, ref.repo_id, ref.resolved_commit)
    return ref, provider, db


def test_extract_contracts_api_level(scanned):
    ref, provider, db = scanned
    contracts = extract_contracts(db, ref.repo_id, ref.resolved_commit)
    qnames = {c["symbol_qname"] for c in contracts}
    assert "company_shared_api.customer_id.normalize_customer_id" in qnames
    assert "company_shared_api.masking.mask_sensitive_fields" in qnames
    assert "company_shared_api.request_signer.sign_request" in qnames
    assert len(contracts) == 3  # API 级：3 个公开函数 = 3 条契约


def test_chunk_api_level_summary_and_docstring(scanned):
    ref, provider, db = scanned
    contract = extract_contracts(db, ref.repo_id, ref.resolved_commit)[0]
    chunks = chunk_contract(contract)
    assert chunks[0][0] == 0
    assert "API:" in chunks[0][1]
    assert "Signature:" in chunks[0][1]
    assert "Source:" in chunks[0][1]


def test_index_and_search_semantic(scanned):
    ref, provider, db = scanned
    stats = index_repository(db, ref.repo_id, ref.resolved_commit, MockEmbedder())
    assert stats["contracts_indexed"] == 3
    assert stats["chunks_stored"] >= 3

    hits = search(db, "customer id normalization", top_k=1, embedder=MockEmbedder())
    assert hits, "应命中至少一条"
    top = hits[0]["provenance"]["symbol"]
    assert "normalize_customer_id" in top

    hits2 = search(db, "sign a request hmac", top_k=1, embedder=MockEmbedder())
    assert "sign_request" in hits2[0]["provenance"]["symbol"]


def test_search_provenance_includes_artifact_after_build(scanned, tmp_path):
    ref, provider, db = scanned
    index_repository(db, ref.repo_id, ref.resolved_commit, MockEmbedder())

    # 模拟已打包：插入 approved proposal + artifact（wheel 路径可推导）
    from git_asset_mcp.packagers.fastapi.generator import build_artifact

    proposal = propose_api(
        db, ref.repo_id, ref.resolved_commit, "company_shared_api",
        entry_symbol="mask_sensitive_fields",
    )
    db.insert_proposal(
        proposal.proposal_id, proposal.module_id, proposal.model_dump_json(),
        "approved", "now",
    )
    proposal.status = "approved"
    build_artifact(proposal, provider, db, "1.0.0", tmp_path / "generated")

    # 重建索引（artifact 出现后 wheel_path 可溯源）
    index_repository(db, ref.repo_id, ref.resolved_commit, MockEmbedder())
    hits = search(db, "mask sensitive fields", top_k=3, embedder=MockEmbedder())
    mask_hits = [h for h in hits if "mask_sensitive_fields" in h["provenance"]["symbol"]]
    assert mask_hits, "脱敏契约应命中"
    assert mask_hits[0]["provenance"]["wheel_path"], "命中应带 wheel 路径溯源"
    assert mask_hits[0]["provenance"]["contract_hash"], "命中应带契约哈希溯源"


def test_index_stats(scanned):
    ref, provider, db = scanned
    index_repository(db, ref.repo_id, ref.resolved_commit, MockEmbedder())
    stats = index_stats(db)
    assert stats["contracts"] == 3
    assert stats["chunks"] >= 3
    assert stats["repositories"] == 1


def test_index_idempotent_rebuild(scanned):
    ref, provider, db = scanned
    index_repository(db, ref.repo_id, ref.resolved_commit, MockEmbedder())
    index_repository(db, ref.repo_id, ref.resolved_commit, MockEmbedder())
    stats = index_stats(db)
    assert stats["contracts"] == 3, "重复索引不应翻倍"
    assert stats["chunks"] >= 3
