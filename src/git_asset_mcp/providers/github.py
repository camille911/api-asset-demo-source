"""GitHub provider implemented on top of the git CLI (no shell=True)."""
from __future__ import annotations

import subprocess
from pathlib import Path

from git_asset_mcp.providers.base import FileChange, RepositoryProvider, RepositoryRef
from git_asset_mcp.security import validate_repository_url


class GithubProvider(RepositoryProvider):
    def __init__(
        self,
        data_dir: Path,
        token: str = "",
        allowed_hosts: tuple[str, ...] = ("github.com",),
        allow_local_paths: bool = False,
    ):
        self._data_dir = data_dir
        self._token = token
        self._allowed_hosts = allowed_hosts
        self._allow_local = allow_local_paths

    # -- helpers -----------------------------------------------------------

    def _repos_dir(self) -> Path:
        return self._data_dir / "repos"

    @staticmethod
    def _repo_id_from_url(url: str) -> str:
        path = url.replace("\\", "/").rstrip("/").removesuffix(".git")
        parts = [p for p in path.split("/") if p]
        if len(parts) >= 2:
            return f"{parts[-2]}-{parts[-1]}"
        return parts[-1] if parts else "repo"

    def _mirror_path(self, repo_id: str) -> Path:
        return self._repos_dir() / f"{repo_id}.git"

    def _git(self, args: list[str], cwd: str | None = None) -> subprocess.CompletedProcess:
        cmd = ["git"]
        if self._token:
            cmd += ["-c", f"http.extraHeader=Authorization: Bearer {self._token}"]
        cmd += args
        return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)

    @staticmethod
    def _ok(proc: subprocess.CompletedProcess) -> bool:
        return proc.returncode == 0

    # -- RepositoryProvider -------------------------------------------------

    def register(self, repository_url: str, ref: str = "main") -> RepositoryRef:
        url = repository_url.strip()
        if not self._allow_local:
            url = validate_repository_url(url, self._allowed_hosts)

        repo_id = self._repo_id_from_url(url)
        mirror = self._mirror_path(repo_id)
        self._repos_dir().mkdir(parents=True, exist_ok=True)

        if mirror.exists():
            self.fetch(repo_id)
        else:
            proc = self._git(["clone", "--bare", "--quiet", url, str(mirror)])
            if not self._ok(proc):
                raise RuntimeError(f"clone failed: {proc.stderr.strip()}")

        commit = self.resolve_commit(repo_id, ref)
        return RepositoryRef(
            repo_id=repo_id,
            repository_url=url,
            default_ref=ref,
            resolved_commit=commit,
            mirror_path=str(mirror),
        )

    def resolve_commit(self, repo_id: str, ref: str) -> str:
        mirror = self._mirror_path(repo_id)
        proc = self._git(["rev-parse", "--verify", ref], cwd=str(mirror))
        if not self._ok(proc):
            proc = self._git(["rev-parse", "--verify", f"refs/remotes/origin/{ref}"], cwd=str(mirror))
        if not self._ok(proc):
            raise RuntimeError(f"cannot resolve ref {ref!r}: {proc.stderr.strip()}")
        return proc.stdout.strip()

    def fetch(self, repo_id: str) -> None:
        mirror = self._mirror_path(repo_id)
        proc = self._git(
            ["fetch", "--quiet", "--prune", "origin", "+refs/heads/*:refs/heads/*"],
            cwd=str(mirror),
        )
        if not self._ok(proc):
            raise RuntimeError(f"fetch failed: {proc.stderr.strip()}")

    def diff(self, repo_id: str, old_commit: str, new_commit: str) -> list[FileChange]:
        mirror = self._mirror_path(repo_id)
        proc = self._git(["diff", "--name-status", old_commit, new_commit], cwd=str(mirror))
        if not self._ok(proc):
            raise RuntimeError(f"diff failed: {proc.stderr.strip()}")

        changes: list[FileChange] = []
        for line in proc.stdout.splitlines():
            parts = line.split("\t", 1)
            if len(parts) != 2:
                continue
            status_code, path = parts[0], parts[1]
            status_map = {"A": "added", "M": "modified", "D": "deleted", "R": "renamed"}
            changes.append(FileChange(path=path, status=status_map.get(status_code[0], "modified")))
        return changes

    def list_files(self, repo_id: str, commit: str) -> list[str]:
        mirror = self._mirror_path(repo_id)
        proc = self._git(["ls-tree", "-r", "--name-only", commit], cwd=str(mirror))
        if not self._ok(proc):
            raise RuntimeError(f"ls-tree failed: {proc.stderr.strip()}")
        return [p for p in proc.stdout.splitlines() if p]

    def ls_tree(self, repo_id: str, commit: str) -> list[tuple[str, str]]:
        mirror = self._mirror_path(repo_id)
        proc = self._git(["ls-tree", "-r", commit], cwd=str(mirror))
        if not self._ok(proc):
            raise RuntimeError(f"ls-tree failed: {proc.stderr.strip()}")
        entries: list[tuple[str, str]] = []
        for line in proc.stdout.splitlines():
            meta, sep, path = line.partition("\t")
            if not sep:
                continue
            parts = meta.split()
            if len(parts) >= 3 and parts[1] == "blob":
                entries.append((path, parts[2]))
        return entries

    def read_blob(self, repo_id: str, blob_sha: str) -> str:
        mirror = self._mirror_path(repo_id)
        proc = self._git(["cat-file", "-p", blob_sha], cwd=str(mirror))
        if not self._ok(proc):
            raise RuntimeError(f"cat-file failed: {proc.stderr.strip()}")
        return proc.stdout
