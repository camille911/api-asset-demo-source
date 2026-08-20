"""Configuration loading: YAML defaults + environment variable overrides.

Secrets (GITHUB_TOKEN, LLM_API_KEY) are read from the environment only and are
never persisted to disk, logged, or stored in the database.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass
class Settings:
    data_dir: Path = Path("./data")
    generated_dir: Path = Path("./generated")
    log_dir: Path = Path("./logs")
    # Secrets — env only, never persisted.
    github_token: str = ""
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    llm_timeout: float = 60.0
    llm_enabled: bool = False
    # Guard rails.
    max_file_bytes: int = 2 * 1024 * 1024
    max_repo_bytes: int = 200 * 1024 * 1024
    allowed_hosts: tuple[str, ...] = ("github.com",)

    @classmethod
    def from_config(cls, config_path: str | Path | None = None) -> "Settings":
        raw: dict[str, Any] = {}
        if config_path:
            path = Path(config_path)
            if path.exists():
                with path.open("r", encoding="utf-8") as fh:
                    loaded = yaml.safe_load(fh)
                    if isinstance(loaded, dict):
                        raw = loaded

        data_dir = Path(_env("DATA_DIR", raw.get("data_dir", "./data")))
        generated_dir = Path(_env("GENERATED_DIR", raw.get("generated_dir", "./generated")))
        log_dir = Path(_env("LOG_DIR", raw.get("log_dir", "./logs")))

        llm = raw.get("llm", {}) if isinstance(raw.get("llm"), dict) else {}
        return cls(
            data_dir=data_dir,
            generated_dir=generated_dir,
            log_dir=log_dir,
            github_token=os.environ.get("GITHUB_TOKEN", ""),
            llm_base_url=_env("LLM_BASE_URL", llm.get("base_url", "")),
            llm_api_key=os.environ.get("LLM_API_KEY", ""),
            llm_model=_env("LLM_MODEL", llm.get("model", "")),
            llm_timeout=float(_env("LLM_TIMEOUT", llm.get("timeout", 60.0))),
            llm_enabled=_to_bool(_env("LLM_ENABLED", llm.get("enabled", False))),
            max_file_bytes=int(_env("MAX_FILE_BYTES", raw.get("max_file_bytes", 2 * 1024 * 1024))),
            max_repo_bytes=int(_env("MAX_REPO_BYTES", raw.get("max_repo_bytes", 200 * 1024 * 1024))),
            allowed_hosts=tuple(raw.get("allowed_hosts", ["github.com"])),
        )


def _env(name: str, default: Any) -> Any:
    return os.environ.get(name, default)


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).lower() in ("1", "true", "yes", "on")
