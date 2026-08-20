"""Verify a generated FastAPI artifact via in-process test client."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient


def _top_level_packages(artifact_dir: Path) -> set[str]:
    """Discover package names under the artifact dir (directories with __init__.py)."""
    pkgs = set()
    for entry in artifact_dir.iterdir():
        if entry.is_dir() and (entry / "__init__.py").exists():
            pkgs.add(entry.name)
    pkgs.add("app")
    return pkgs


def load_generated_app(artifact_dir: Path):
    """Load the ``app`` FastAPI instance from a generated artifact directory."""
    artifact_dir = Path(artifact_dir)
    if str(artifact_dir) not in sys.path:
        sys.path.insert(0, str(artifact_dir))
    # Clear stale modules (app + dependency-closure packages) from previous loads.
    for pkg in _top_level_packages(artifact_dir):
        for name in list(sys.modules):
            if name == pkg or name.startswith(pkg + "."):
                del sys.modules[name]
    import app.main  # type: ignore

    return app.main.app


def verify_artifact(artifact_dir: Path, example_request: dict | None = None) -> dict:
    checks: list[dict] = []

    try:
        app = load_generated_app(artifact_dir)
        checks.append({"name": "import", "ok": True})
    except Exception as exc:  # pragma: no cover - import failures
        return {"status": "failed", "checks": [{"name": "import", "ok": False, "detail": str(exc)}]}

    client = TestClient(app)

    r = client.get("/health")
    checks.append(
        {"name": "health", "ok": r.status_code == 200 and r.json().get("status") == "ok"}
    )

    r = client.get("/metadata")
    ok = r.status_code == 200 and "asset_id" in r.json()
    checks.append({"name": "metadata", "ok": ok})

    r = client.get("/openapi.json")
    checks.append({"name": "openapi", "ok": r.status_code == 200 and "paths" in r.json()})

    golden_vector = None
    if example_request is not None:
        from app.main import app as _app  # noqa: F401

        # Find the business endpoint path from OpenAPI (first non health/metadata path).
        paths = [p for p in r.json().get("paths", {}) if p not in ("/health", "/metadata")]
        business_ok = False
        if paths:
            path = paths[0]
            method = next(iter(r.json()["paths"][path]))
            resp = client.request(method, path, json={"payload": example_request})
            result = resp.json().get("result") if resp.status_code == 200 else None
            # A real call returns business fields; a stub echoes the input back.
            echoed = result == example_request
            business_ok = result is not None and not echoed
            checks.append({"name": "business", "ok": business_ok, "detail": result})
            if business_ok:
                golden_vector = {"input": example_request, "output": result}

    status = "passed" if all(c["ok"] for c in checks) else "failed"

    # Persist a golden vector when the business call actually produced a result.
    if golden_vector is not None:
        out = Path(artifact_dir) / "app" / "contract" / "golden-vectors.json"
        out.write_text(json.dumps([golden_vector], indent=2, ensure_ascii=False), encoding="utf-8")

    return {"status": status, "checks": checks, "golden_vector": golden_vector}
