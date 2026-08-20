"""Integration test: FastAPI packaging with approval/version/dedup guards."""
import subprocess

import pytest

from git_asset_mcp.analyzers.python.inventory import scan_repository
from git_asset_mcp.packagers.fastapi.generator import build_artifact
from git_asset_mcp.proposal.proposer import propose_api
from git_asset_mcp.providers.github import GithubProvider
from git_asset_mcp.store.database import Database


def _git(args, cwd):
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)


VALIDATION_SRC = (
    '"""Order validation."""\n'
    "\n"
    "def validate_order(order: dict) -> None:\n"
    '    """Validate an order."""\n'
    "    _check(order)\n"
    "\n"
    "def _check(order) -> None:\n"
    "    if not order:\n"
    '        raise ValueError("empty")\n'
)


@pytest.fixture
def scanned(tmp_path):
    repo = tmp_path / "source"
    repo.mkdir()
    _git(["init", "-q", "-b", "main"], repo)
    _git(["config", "user.email", "t@e.com"], repo)
    _git(["config", "user.name", "t"], repo)
    pkg = repo / "order_api"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "validation.py").write_text(VALIDATION_SRC)
    _git(["add", "."], repo)
    _git(["commit", "-q", "-m", "init"], repo)

    provider = GithubProvider(data_dir=tmp_path / "data", allow_local_paths=True)
    ref = provider.register(str(repo), "main")
    db = Database(tmp_path / "data" / "scan.db")
    scan_repository(provider, db, ref.repo_id, ref.resolved_commit)
    return ref, provider, db


def _approved(db, ref):
    proposal = propose_api(db, ref.repo_id, ref.resolved_commit, "order_api")
    db.insert_proposal(
        proposal.proposal_id, proposal.module_id, proposal.model_dump_json(), "approved", "now"
    )
    proposal.status = "approved"
    return proposal


def test_build_rejects_unapproved(scanned, tmp_path):
    ref, provider, db = scanned
    proposal = propose_api(db, ref.repo_id, ref.resolved_commit, "order_api")
    assert proposal.status == "proposed"
    with pytest.raises(RuntimeError, match="proposal_not_approved"):
        build_artifact(proposal, provider, db, "1.0.0", tmp_path / "generated")


def test_build_creates_artifact(scanned, tmp_path):
    ref, provider, db = scanned
    proposal = _approved(db, ref)
    result = build_artifact(proposal, provider, db, "1.0.0", tmp_path / "generated")

    assert result["build_status"] == "ok"
    assert len(result["contract_hash"]) == 64
    assert len(result["implementation_hash"]) == 64
    main = tmp_path / "generated" / proposal.api_name / "1.0.0" / "app" / "main.py"
    assert main.exists()
    assert (tmp_path / "generated" / proposal.api_name / "1.0.0" / "asset-manifest.yaml").exists()
    assert (tmp_path / "generated" / proposal.api_name / "1.0.0" / "source-provenance.json").exists()


def test_build_rejects_duplicate_version(scanned, tmp_path):
    ref, provider, db = scanned
    proposal = _approved(db, ref)
    build_artifact(proposal, provider, db, "1.0.0", tmp_path / "generated")
    with pytest.raises(RuntimeError, match="version_exists"):
        build_artifact(proposal, provider, db, "1.0.0", tmp_path / "generated")


def test_build_allows_version_upgrade(scanned, tmp_path):
    ref, provider, db = scanned
    p1 = _approved(db, ref)
    build_artifact(p1, provider, db, "1.0.0", tmp_path / "generated")

    # 同一模块的新版本（1.0.0 -> 1.1.0）是版本升级，允许。
    p2 = propose_api(db, ref.repo_id, ref.resolved_commit, "order_api")
    db.insert_proposal(p2.proposal_id, p2.module_id, p2.model_dump_json(), "approved", "now")
    p2.status = "approved"
    result = build_artifact(p2, provider, db, "1.1.0", tmp_path / "generated")
    assert result["build_status"] == "ok"


def test_build_rejects_cross_module_duplicate(scanned, tmp_path):
    ref, provider, db = scanned
    proposal = _approved(db, ref)  # module_id = <repo>:order_api

    # 手动插入一个「其他模块」的制品，入口符号集与当前 proposal 相同，
    # 模拟跨模块复用同一能力（符号来源已被其他资产覆盖）。
    fake_proposal_id = "fake-other-module"
    db.insert_proposal(
        fake_proposal_id, f"{ref.repo_id}:other_module", "{}", "approved", "now"
    )
    db.insert_artifact(
        artifact_id="fake-artifact",
        proposal_id=fake_proposal_id,
        semantic_version="1.0.0",
        source_commit=ref.resolved_commit,
        contract_hash="x" * 64,
        implementation_hash="y" * 64,
        artifact_path=str(tmp_path / "other"),
        verification_status="verified",
        created_at="now",
        entry_symbols=sorted(proposal.entry_symbols),
    )

    with pytest.raises(RuntimeError, match="duplicate_asset"):
        build_artifact(proposal, provider, db, "1.0.0", tmp_path / "generated")
