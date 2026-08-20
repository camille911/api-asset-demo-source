"""Contract and implementation hashing (task book 13)."""
from __future__ import annotations

import hashlib
import json


def canonical_json(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def contract_hash(openapi: dict) -> str:
    """SHA-256 over canonicalized contract fields of OpenAPI.

    Only paths and schemas contribute; volatile fields (server URLs, titles,
    generation timestamps) are excluded.
    """
    contract = {
        "paths": openapi.get("paths", {}),
        "components": {
            "schemas": openapi.get("components", {}).get("schemas", {})
        },
    }
    return hashlib.sha256(canonical_json(contract).encode("utf-8")).hexdigest()


def implementation_hash(source_blob_shas: list[str], adapter_source: str, template_version: str) -> str:
    """SHA-256 over source blob SHAs + adapter content + template version."""
    payload = canonical_json(
        {
            "blob_shas": sorted(source_blob_shas),
            "adapter": adapter_source,
            "template_version": template_version,
        }
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
