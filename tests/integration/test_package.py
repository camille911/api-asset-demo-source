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


# ---------------------------------------------------------------------------
# 方案 B：entry_symbol 入口选择 + 文件级闭包（多功能独立文件）
# ---------------------------------------------------------------------------

MULTI_SRC = {
    "customer_id.py": (
        '"""Customer identifier normalization."""\n'
        "import re\n"
        "\n"
        "def normalize_customer_id(value: str) -> str:\n"
        '    """Normalize a customer id."""\n'
        '    return re.sub(r"[\\s-]+", "", value).upper()\n'
    ),
    "masking.py": (
        '"""Sensitive field masking."""\n'
        "from copy import deepcopy\n"
        "\n"
        "def mask_sensitive_fields(payload: dict, fields: set) -> dict:\n"
        '    """Mask selected fields."""\n'
        "    return deepcopy(payload)\n"
    ),
    "request_signer.py": (
        '"""Request signing."""\n'
        "import hmac\n"
        "\n"
        "def sign_request(payload: dict, secret: str) -> str:\n"
        '    """Sign a request."""\n'
        '    return "sig"\n'
    ),
}


@pytest.fixture
def multi_scanned(tmp_path):
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


def _approved_proposal(db, ref, **kwargs):
    proposal = propose_api(db, ref.repo_id, ref.resolved_commit, "company_shared_api", **kwargs)
    db.insert_proposal(
        proposal.proposal_id, proposal.module_id, proposal.model_dump_json(), "approved", "now"
    )
    proposal.status = "approved"
    return proposal


def test_proposal_selects_target_entry(multi_scanned):
    ref, provider, db = multi_scanned
    prop = propose_api(
        db, ref.repo_id, ref.resolved_commit, "company_shared_api",
        entry_symbol="mask_sensitive_fields",
    )
    assert prop.api_name == "mask_sensitive_fields"
    assert prop.entry_symbols[0] == "company_shared_api.masking.mask_sensitive_fields"


def test_proposal_defaults_to_first_entry(multi_scanned):
    ref, provider, db = multi_scanned
    prop = propose_api(db, ref.repo_id, ref.resolved_commit, "company_shared_api")
    assert prop.entry_symbols[0] == "company_shared_api.customer_id.normalize_customer_id"


def test_proposal_unknown_entry_raises(multi_scanned):
    ref, provider, db = multi_scanned
    with pytest.raises(ValueError, match="entry_symbol"):
        propose_api(
            db, ref.repo_id, ref.resolved_commit, "company_shared_api",
            entry_symbol="does_not_exist",
        )


def test_package_file_closure_only_entry_file(multi_scanned, tmp_path):
    ref, provider, db = multi_scanned
    prop = _approved_proposal(db, ref, entry_symbol="mask_sensitive_fields")
    result = build_artifact(prop, provider, db, "1.0.0", tmp_path / "generated")
    assert result["build_status"] == "ok"

    pkg_out = tmp_path / "generated" / prop.api_name / "1.0.0" / "company_shared_api"
    assert (pkg_out / "masking.py").exists(), "入口文件应被打包"
    assert (pkg_out / "__init__.py").exists(), "包 __init__ 应保留"
    assert not (pkg_out / "customer_id.py").exists(), "无关功能文件不应进入 wheel"
    assert not (pkg_out / "request_signer.py").exists(), "无关功能文件不应进入 wheel"


def test_package_legacy_closure_backward_compatible(scanned, tmp_path):
    """不传 entry_symbol 时，闭包行为与旧版一致（目录级回退不破坏单文件包）。"""
    ref, provider, db = scanned
    prop = _approved(db, ref)
    result = build_artifact(prop, provider, db, "1.0.0", tmp_path / "generated")
    assert result["build_status"] == "ok"
    pkg_out = tmp_path / "generated" / prop.api_name / "1.0.0" / "order_api"
    assert (pkg_out / "validation.py").exists()
    assert (pkg_out / "__init__.py").exists()
