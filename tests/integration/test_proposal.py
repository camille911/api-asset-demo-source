"""Integration test: deterministic API proposal + approval state machine."""
import subprocess

import pytest

from git_asset_mcp.analyzers.python.inventory import scan_repository
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
    return ref, db


def test_propose_creates_proposed(scanned):
    ref, db = scanned
    proposal = propose_api(db, ref.repo_id, ref.resolved_commit, "order_api")

    assert proposal.status == "proposed"
    assert proposal.requires_confirmation is True
    assert any("validate_order" in e for e in proposal.entry_symbols)
    assert not any(e.endswith("_check") for e in proposal.entry_symbols)
    assert proposal.method == "POST"
    assert proposal.path.startswith("/v1/")


def test_propose_rejects_unknown_module(scanned):
    ref, db = scanned
    with pytest.raises(ValueError):
        propose_api(db, ref.repo_id, ref.resolved_commit, "nonexistent")


def test_proposal_approve_state_machine(scanned):
    ref, db = scanned
    proposal = propose_api(db, ref.repo_id, ref.resolved_commit, "order_api")
    db.insert_proposal(
        proposal.proposal_id, proposal.module_id, proposal.model_dump_json(), "proposed", "now"
    )
    db.update_proposal_status(proposal.proposal_id, "approved")
    assert db.get_proposal(proposal.proposal_id)["status"] == "approved"
