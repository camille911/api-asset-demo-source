"""Integration test: verify a generated FastAPI artifact runs and passes checks."""
import subprocess

import pytest

from git_asset_mcp.analyzers.python.inventory import scan_repository
from git_asset_mcp.packagers.fastapi.generator import build_artifact
from git_asset_mcp.packagers.fastapi.verifier import verify_artifact
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
def built_artifact(tmp_path):
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

    proposal = propose_api(db, ref.repo_id, ref.resolved_commit, "order_api")
    proposal.status = "approved"
    build_artifact(proposal, provider, db, "1.0.0", tmp_path / "generated")
    return tmp_path / "generated" / proposal.api_name / "1.0.0"


def test_verify_passes(built_artifact):
    result = verify_artifact(built_artifact)
    assert result["status"] == "passed"
    names = {c["name"] for c in result["checks"]}
    assert {"import", "health", "metadata", "openapi"} <= names


def test_business_endpoint_responds(built_artifact):
    from git_asset_mcp.packagers.fastapi.verifier import load_generated_app
    from fastapi.testclient import TestClient

    app = load_generated_app(built_artifact)
    client = TestClient(app)
    resp = client.post("/v1/order-api/validate-order", json={"payload": {"order_id": "ORD-1"}})
    assert resp.status_code == 200
    assert "result" in resp.json()
