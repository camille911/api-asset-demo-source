"""Repository provider abstraction (stable extension point)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class RepositoryRef:
    repo_id: str
    repository_url: str
    default_ref: str
    resolved_commit: str
    mirror_path: str


@dataclass
class FileChange:
    path: str
    status: str  # added | modified | deleted | renamed


class RepositoryProvider(ABC):
    """Abstraction over a remote git host. GitHub is the first implementation."""

    @abstractmethod
    def register(self, repository_url: str, ref: str) -> RepositoryRef:
        """Create (or reuse) a local read-only mirror and resolve ``ref`` to a commit."""

    @abstractmethod
    def resolve_commit(self, repo_id: str, ref: str) -> str:
        """Resolve a ref name to a full commit SHA."""

    @abstractmethod
    def fetch(self, repo_id: str) -> None:
        """Update the local mirror from the remote without re-cloning."""

    @abstractmethod
    def diff(self, repo_id: str, old_commit: str, new_commit: str) -> list[FileChange]:
        """List file-level changes between two commits."""

    @abstractmethod
    def list_files(self, repo_id: str, commit: str) -> list[str]:
        """List all file paths in the tree of ``commit``."""

    @abstractmethod
    def ls_tree(self, repo_id: str, commit: str) -> list[tuple[str, str]]:
        """Return ``(path, blob_sha)`` pairs for all blobs in the tree of ``commit``."""

    @abstractmethod
    def read_blob(self, repo_id: str, blob_sha: str) -> str:
        """Read the content of a blob by its SHA."""
