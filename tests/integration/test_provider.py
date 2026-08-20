"""Integration tests for GithubProvider using a local git fixture (no real GitHub)."""
import subprocess

import pytest

from git_asset_mcp.providers.github import GithubProvider


def _git(args, cwd):
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)


@pytest.fixture
def local_repo(tmp_path):
    repo = tmp_path / "source"
    repo.mkdir()
    _git(["init", "-q", "-b", "main"], repo)
    _git(["config", "user.email", "test@example.com"], repo)
    _git(["config", "user.name", "test"], repo)
    (repo / "a.py").write_text("def foo():\n    return 1\n")
    _git(["add", "."], repo)
    _git(["commit", "-q", "-m", "init"], repo)
    return repo


def test_register_and_resolve(local_repo, tmp_path):
    provider = GithubProvider(data_dir=tmp_path / "data", allow_local_paths=True)
    result = provider.register(str(local_repo), ref="main")
    assert result.repo_id
    assert result.resolved_commit
    assert len(result.resolved_commit) == 40


def test_register_reuses_mirror(local_repo, tmp_path):
    provider = GithubProvider(data_dir=tmp_path / "data", allow_local_paths=True)
    first = provider.register(str(local_repo), ref="main")
    second = provider.register(str(local_repo), ref="main")
    assert first.resolved_commit == second.resolved_commit
    assert first.mirror_path == second.mirror_path


def test_diff_detects_modification(local_repo, tmp_path):
    provider = GithubProvider(data_dir=tmp_path / "data", allow_local_paths=True)
    result = provider.register(str(local_repo), ref="main")
    old_commit = result.resolved_commit

    (local_repo / "a.py").write_text("def foo():\n    return 2\n")
    _git(["add", "."], local_repo)
    _git(["commit", "-q", "-m", "change"], local_repo)
    provider.fetch(result.repo_id)
    new_commit = provider.resolve_commit(result.repo_id, "main")

    changes = provider.diff(result.repo_id, old_commit, new_commit)
    assert any(c.path == "a.py" and c.status == "modified" for c in changes)


def test_list_files(local_repo, tmp_path):
    provider = GithubProvider(data_dir=tmp_path / "data", allow_local_paths=True)
    result = provider.register(str(local_repo), ref="main")
    files = provider.list_files(result.repo_id, result.resolved_commit)
    assert "a.py" in files
