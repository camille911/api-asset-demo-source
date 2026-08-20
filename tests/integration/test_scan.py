"""Integration test: scan a local git fixture and persist symbols."""
import subprocess

import pytest

from git_asset_mcp.analyzers.python.inventory import scan_repository
from git_asset_mcp.analyzers.python.module_detector import detect_modules
from git_asset_mcp.providers.github import GithubProvider
from git_asset_mcp.store.database import Database


def _git(args, cwd):
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)


VALIDATION_SRC = (
    '"""Order validation."""\n'
    "\n"
    "class OrderValidationError(ValueError):\n"
    '    """Invalid order."""\n'
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
def python_repo(tmp_path):
    repo = tmp_path / "source"
    repo.mkdir()
    _git(["init", "-q", "-b", "main"], repo)
    _git(["config", "user.email", "t@e.com"], repo)
    _git(["config", "user.name", "t"], repo)
    pkg = repo / "order_api"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "validation.py").write_text(VALIDATION_SRC)
    tests = repo / "tests"
    tests.mkdir()
    (tests / "test_validation.py").write_text("def test_x():\n    pass\n")
    _git(["add", "."], repo)
    _git(["commit", "-q", "-m", "init"], repo)
    return repo


def test_scan_extracts_symbols(python_repo, tmp_path):
    provider = GithubProvider(data_dir=tmp_path / "data", allow_local_paths=True)
    ref = provider.register(str(python_repo), "main")
    db = Database(tmp_path / "data" / "scan.db")

    result = scan_repository(provider, db, ref.repo_id, ref.resolved_commit)

    assert result["files_ok"] >= 1
    # module + class + 2 functions = 4 symbols（测试文件被排除）
    assert result["symbols_total"] >= 4
    assert db.count_symbols(ref.repo_id, ref.resolved_commit) >= 4


def test_scan_excludes_test_files(python_repo, tmp_path):
    provider = GithubProvider(data_dir=tmp_path / "data", allow_local_paths=True)
    ref = provider.register(str(python_repo), "main")
    db = Database(tmp_path / "data" / "scan.db")

    scan_repository(provider, db, ref.repo_id, ref.resolved_commit)

    rows = db._conn.execute(
        "SELECT path FROM files WHERE repo_id = ? AND commit_sha = ?",
        (ref.repo_id, ref.resolved_commit),
    ).fetchall()
    paths = {r["path"] for r in rows}
    assert "tests/test_validation.py" not in paths
    assert "order_api/validation.py" in paths


def test_detect_modules(python_repo, tmp_path):
    provider = GithubProvider(data_dir=tmp_path / "data", allow_local_paths=True)
    ref = provider.register(str(python_repo), "main")
    db = Database(tmp_path / "data" / "scan.db")
    scan_repository(provider, db, ref.repo_id, ref.resolved_commit)

    modules = detect_modules(db, ref.repo_id, ref.resolved_commit)
    names = [m["name"] for m in modules]
    assert any("order_api" in n for n in names)

    order_module = next(m for m in modules if "order_api" in m["name"])
    assert order_module["symbol_count"] >= 3  # class + 2 functions
    assert any(e.endswith("validate_order") for e in order_module["entry_symbols"])
