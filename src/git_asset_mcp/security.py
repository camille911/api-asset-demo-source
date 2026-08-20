"""Security helpers: repository URL validation and secret redaction."""
from __future__ import annotations

import re
from urllib.parse import urlparse

ALLOWED_SCHEMES = ("https",)
DEFAULT_ALLOWED_HOSTS = ("github.com",)

# GitHub personal access token shapes.
_GITHUB_PAT = re.compile(r"\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b")
_BEARER = re.compile(r"(Bearer\s+)[A-Za-z0-9._~-]+", re.IGNORECASE)
_URL_CREDS = re.compile(r"(https?://)[^/@\s]+@")
_GENERIC_TOKEN = re.compile(r"(token|api[_-]?key|secret|password)\s*[:=]\s*[^\s,;]+", re.IGNORECASE)


def validate_repository_url(url: str, allowed_hosts: tuple[str, ...] = DEFAULT_ALLOWED_HOSTS) -> str:
    """Validate a repository URL and return a normalized form.

    Only https URLs to allowed hosts are accepted. ``file://``, ``ssh`` and any
    other scheme are rejected to prevent arbitrary protocol / command injection.
    """
    if not isinstance(url, str) or not url.strip():
        raise ValueError("repository URL must be a non-empty string")

    candidate = url.strip()
    parsed = urlparse(candidate)

    if parsed.scheme not in ALLOWED_SCHEMES:
        raise ValueError(f"unsupported scheme: {parsed.scheme!r} (only https allowed)")

    host = (parsed.hostname or "").lower()
    if not host or host not in allowed_hosts:
        raise ValueError(f"host not allowed: {host!r}")

    if parsed.username or parsed.password:
        raise ValueError("repository URL must not embed credentials")

    # Normalize: strip trailing slash and query/fragment.
    path = parsed.path.rstrip("/")
    normalized = f"https://{host}{path}"
    return normalized


def redact_secrets(text: str) -> str:
    """Redact tokens and credentials from ``text`` for safe logging/output."""
    if not text:
        return text
    out = _URL_CREDS.sub(r"\1***@", text)
    out = _GITHUB_PAT.sub(r"\1_***REDACTED***", out)
    out = _BEARER.sub(r"\1***REDACTED***", out)
    out = _GENERIC_TOKEN.sub(r"\1=***REDACTED***", out)
    return out
