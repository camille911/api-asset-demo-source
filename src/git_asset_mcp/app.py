"""Application context holding runtime dependencies (provider, store, ...)."""
from __future__ import annotations

from dataclasses import dataclass

from git_asset_mcp.providers.github import GithubProvider
from git_asset_mcp.settings import Settings
from git_asset_mcp.store.database import Database


@dataclass
class AppContext:
    settings: Settings
    provider: GithubProvider
    db: Database

    @classmethod
    def build(cls, config_path: str | None = None) -> "AppContext":
        settings = Settings.from_config(config_path)
        provider = GithubProvider(
            data_dir=settings.data_dir,
            token=settings.github_token,
            allowed_hosts=settings.allowed_hosts,
        )
        db = Database(settings.data_dir / "metadata.db")
        return cls(settings=settings, provider=provider, db=db)
