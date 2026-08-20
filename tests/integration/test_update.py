"""Integration test: incremental update detection and version planning."""
import subprocess

import pytest

from git_asset_mcp.analyzers.python.inventory import scan_repository
from git_asset_mcp.packagers.fastapi.generator import build_artifact
from git_asset_mcp.proposal.proposer import propose_api
from git_asset_mcp.providers.github import GithubProvider
from git_asset_mcp.store.database import Database
from git_asset_mcp.updater import update_check, update_plan


def _git(args, cwd):
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)


def _commit(repo, content):
    (repo / "order_api" / "validation.py").write_text(content)
    _git(["add", "."], repo)
    _git(["commit", "-q", "-m", "change"], repo)


@pytest.fixture
def setup(tmp_path):
    repo = tmp_path / "source"
    repo.mkdir()
    _git(["init", "-q", "-b", "main"], repo)
    _git(["config", "user.email", "t@e.com"], repo)
    _git(["config", "user.name", "t"], repo)
    pkg = repo / "order_api"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "validation.py").write_text(
        'def validate_order(order: dict) -> None:\n'
        '    """Validate an order."""\n'
        '    if not order.get("order_id"):\n'
        '        raise ValueError("missing")\n'
    )
    _git(["add", "."], repo)
    _git(["commit", "-q", "-m", "init"], repo)

    provider = GithubProvider(data_dir=tmp_path / "data", allow_local_paths=True)
    ref = provider.register(str(repo), "main")
    db = Database(tmp_path / "data" / "scan.db")
    scan_repository(provider, db, ref.repo_id, ref.resolved_commit)
    return repo, provider, ref, db, tmp_path


def _build_approved(provider, db, ref, tmp_path):
    proposal = propose_api(db, ref.repo_id, ref.resolved_commit, "order_api")
    proposal.status = "approved"
    db.insert_proposal(proposal.proposal_id, proposal.module_id, proposal.model_dump_json(), "approved", "now")
    built = build_artifact(proposal, provider, db, "1.0.0", tmp_path / "generated")
    return proposal, built


def _rescan(provider, db, ref):
    provider.fetch(ref.repo_id)
    new_commit = provider.resolve_commit(ref.repo_id, "main")
    scan_repository(provider, db, ref.repo_id, new_commit)
    return new_commit


def test_update_check_detects_changes(setup):
    repo, provider, ref, db, tmp_path = setup
    _commit(
        repo,
        'def validate_order(order: dict) -> None:\n    """V."""\n    if not order:\n        raise ValueError("x")\n',
    )
    result = update_check(provider, db, ref.repo_id, "main")
    assert result["has_changes"] is True
    assert "order_api/validation.py" in result["changed_files"]


def test_update_plan_patch_when_contract_unchanged(setup):
    repo, provider, ref, db, tmp_path = setup
    _, built = _build_approved(provider, db, ref, tmp_path)

    # 实验 A：内部实现变化，签名/契约不变
    _commit(
        repo,
        'def validate_order(order: dict) -> None:\n    """Validate."""\n    if order.get("order_id") == "":\n        raise ValueError("empty")\n',
    )
    _rescan(provider, db, ref)

    plan = update_plan(provider, db, built["artifact_id"])
    assert plan["contract_changed"] is False
    assert plan["implementation_changed"] is True
    assert plan["compatibility"] == "compatible"
    assert plan["recommended_version"] == "patch"


def test_update_plan_breaking_when_contract_changes(setup):
    repo, provider, ref, db, tmp_path = setup
    _, built = _build_approved(provider, db, ref, tmp_path)

    # 实验 B：破坏性契约变化（新增必填参数）
    _commit(
        repo,
        'def validate_order(order: dict, strict: bool = False) -> None:\n    """Validate."""\n    pass\n',
    )
    _rescan(provider, db, ref)

    plan = update_plan(provider, db, built["artifact_id"])
    assert plan["contract_changed"] is True
    assert plan["compatibility"] == "breaking"
    assert plan["recommended_version"] == "major"
