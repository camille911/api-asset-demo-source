"""FastAPI artifact generator: dependency closure + adapter + contract + wheel.

Produces a "three-part wheel" for each approved API:
  1. runnable app  (``app/``)          — FastAPI service + stable schemas + adapter
  2. dependency closure (``<pkg>/``)   — legacy source copied in as importable package
  3. readable contract (``app/contract/``) — api-contract.json + provenance
"""
from __future__ import annotations

import datetime
import json
import re
import subprocess
import sys
import uuid
from pathlib import Path

import yaml

from git_asset_mcp.packagers.fastapi.contract import contract_hash, implementation_hash
from git_asset_mcp.proposal.schemas import ApiProposal
from git_asset_mcp.providers.base import RepositoryProvider
from git_asset_mcp.store.database import Database

TEMPLATE_VERSION = "0.1.0"


def _minimal_openapi(proposal: ApiProposal, api_name: str, version: str) -> dict:
    return {
        "openapi": "3.1.0",
        "info": {"title": api_name, "version": version},
        "paths": {
            proposal.path: {
                proposal.method.lower(): {
                    "requestBody": {
                        "content": {"application/json": {"schema": proposal.request_schema}}
                    },
                    "responses": {
                        "200": {
                            "description": "ok",
                            "content": {
                                "application/json": {"schema": proposal.response_schema}
                            },
                        }
                    },
                }
            }
        },
        "components": {"schemas": {}},
    }


# ---------------------------------------------------------------------------
# dependency closure
# ---------------------------------------------------------------------------

def _closure_info(entry_path: str) -> tuple[str, str, str]:
    """Derive ``(pkg_dir, package_name, import_module)`` from the entry file path.

    Works with or without a leading source-root segment:
      ``src/legacy_checkout/checkout.py`` -> package ``legacy_checkout``, module ``legacy_checkout.checkout``
      ``order_api/validation.py``         -> package ``order_api``,        module ``order_api.validation``
    """
    parts = entry_path.replace("\\", "/").split("/")
    pkg_dir = "/".join(parts[:-1])
    package_name = pkg_dir.split("/")[-1]
    filename = parts[-1]
    submodule = filename[:-3] if filename.endswith(".py") else filename
    import_module = f"{package_name}.{submodule}" if submodule else package_name
    return pkg_dir, package_name, import_module


def _package_closure(
    provider: RepositoryProvider, repo_id: str, commit: str,
    pkg_dir: str, package_name: str,
) -> tuple[dict[str, str], list[str]]:
    """Copy the legacy package (containing the entry) into the artifact.

    Returns ``(files, blob_shas)`` where ``files`` maps the wheel-relative path
    (under ``package_name``) to source text.
    """
    files: dict[str, str] = {}
    blob_shas: list[str] = []
    for path, blob_sha in provider.ls_tree(repo_id, commit):
        if path.startswith(pkg_dir + "/") and path.endswith(".py"):
            source = provider.read_blob(repo_id, blob_sha)
            rel_in_pkg = path[len(pkg_dir) + 1:]
            files[f"{package_name}/{rel_in_pkg}"] = source
            blob_shas.append(blob_sha)
    return files, blob_shas


def _parse_entry_params(signature: str) -> list[tuple[str, str | None]]:
    """Parse ``def f(a, b='x', c=None) -> ...`` into [(name, default_literal_or_None)]."""
    m = re.search(r"\((.*)\)", signature)
    if not m:
        return []
    params: list[tuple[str, str | None]] = []
    for p in m.group(1).split(","):
        p = p.strip()
        if not p or p == "self":
            continue
        default = None
        name_part = p
        if "=" in p:
            name_part, default = p.split("=", 1)
            name_part = name_part.strip()
            default = default.strip()
        if ":" in name_part:
            name_part = name_part.split(":", 1)[0].strip()
        if name_part:
            params.append((name_part, default))
    return params


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------

def build_artifact(
    proposal: ApiProposal,
    provider: RepositoryProvider,
    db: Database,
    version: str,
    generated_dir: Path,
    build_wheel: bool = False,
) -> dict:
    """Package an approved proposal into an immutable version directory (+ optional wheel)."""
    if proposal.status != "approved":
        raise RuntimeError("proposal_not_approved")

    artifact_dir = generated_dir / proposal.api_name / version
    if artifact_dir.exists():
        raise RuntimeError("version_exists")

    existing = db.find_artifact_by_symbols(sorted(proposal.entry_symbols))
    if existing and existing.get("module_id") != proposal.module_id:
        raise RuntimeError(f"duplicate_asset: already covered by {existing['artifact_id']}")

    repo_id = proposal.module_id.split(":", 1)[0]
    commit = proposal.source_paths and _commit_for(db, repo_id)

    entry_qname = proposal.entry_symbols[0]
    pkg_dir, package_name, import_module = _closure_info(proposal.source_paths[0])
    func_name = entry_qname.split(".")[-1]
    signature = db.get_symbol_signature(repo_id, commit or "", entry_qname) or ""
    params = _parse_entry_params(signature)

    # dependency closure (legacy source) + its blob shas
    closure_files, closure_blobs = _package_closure(
        provider, repo_id, commit or "", pkg_dir, package_name
    )
    entry_blobs = db.blob_shas_for_paths(repo_id, commit or "", proposal.source_paths) if commit else []
    all_blobs = sorted(set(entry_blobs + closure_blobs))

    artifact_dir.mkdir(parents=True, exist_ok=False)
    app_dir = artifact_dir / "app"
    (app_dir / "contract").mkdir(parents=True)
    (app_dir / "__init__.py").write_text("", encoding="utf-8")

    adapter_source = _render_adapter(import_module, func_name, params)
    (app_dir / "adapter.py").write_text(adapter_source, encoding="utf-8")
    (app_dir / "schemas.py").write_text(_render_schemas(proposal), encoding="utf-8")

    openapi = _minimal_openapi(proposal, proposal.api_name, version)
    chash = contract_hash(openapi)
    ihash = implementation_hash(all_blobs, adapter_source, TEMPLATE_VERSION)

    (app_dir / "main.py").write_text(
        _render_main(proposal, version, chash, ihash, commit or ""), encoding="utf-8"
    )

    # dependency closure package (importable, keeps original import statements intact)
    for rel, source in closure_files.items():
        out = artifact_dir / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(source, encoding="utf-8")

    # readable contract + provenance (inside the app package so they ship in the wheel)
    (app_dir / "contract" / "api-contract.json").write_text(
        _render_contract(proposal, import_module, func_name, signature, params, version),
        encoding="utf-8",
    )
    (app_dir / "contract" / "source-provenance.json").write_text(
        _render_provenance(proposal, version, commit or "", all_blobs, closure_files), encoding="utf-8"
    )

    # top-level artifacts for inspection (also duplicated into the wheel via package-data)
    (artifact_dir / "openapi.json").write_text(_json_dumps(openapi), encoding="utf-8")
    (artifact_dir / "asset-manifest.yaml").write_text(
        _render_manifest(proposal, version, chash, ihash, commit or ""), encoding="utf-8"
    )
    (artifact_dir / "source-provenance.json").write_text(
        _render_provenance(proposal, version, commit or "", all_blobs, closure_files), encoding="utf-8"
    )
    (artifact_dir / "pyproject.toml").write_text(
        _render_pyproject(proposal, version, ["app", package_name] if package_name else ["app"]),
        encoding="utf-8",
    )

    # build the wheel into dist/ (optional; off by default to keep tests fast)
    wheel_path = _build_wheel(artifact_dir, generated_dir.parent / "dist") if build_wheel else ""

    artifact_id = str(uuid.uuid4())
    db.insert_artifact(
        artifact_id=artifact_id,
        proposal_id=proposal.proposal_id,
        semantic_version=version,
        source_commit=commit or "",
        contract_hash=chash,
        implementation_hash=ihash,
        artifact_path=str(artifact_dir),
        verification_status="unverified",
        created_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        entry_symbols=sorted(proposal.entry_symbols),
    )

    return {
        "artifact_id": artifact_id,
        "artifact_path": str(artifact_dir),
        "wheel_path": wheel_path,
        "source_commit": commit or "",
        "version": version,
        "contract_hash": chash,
        "implementation_hash": ihash,
        "build_status": "ok",
    }


def _commit_for(db: Database, repo_id: str) -> str | None:
    return db.get_last_scanned_commit(repo_id)


def _json_dumps(obj: dict) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False)


def _build_wheel(artifact_dir: Path, dist_dir: Path) -> str:
    """Build a wheel, routing setuptools' temp dir through the OS temp area.

    Some sandboxes hook ``shutil.rmtree`` and block deletions outside the OS
    temp dir, which breaks setuptools' own temp-dir cleanup. Building into an
    OS-temp outdir (then copying the wheel out) keeps that cleanup in the
    exempt zone.
    """
    import shutil
    import tempfile

    tmp_out = Path(tempfile.mkdtemp(prefix="whl-out-"))
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "build", "--wheel", "--no-isolation", "--outdir", str(tmp_out)],
            cwd=str(artifact_dir),
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"wheel build failed: {proc.stderr[-600:]}")
        wheels = sorted(tmp_out.glob("*.whl"))
        if not wheels:
            return ""
        dist_dir.mkdir(parents=True, exist_ok=True)
        target = dist_dir / wheels[-1].name
        shutil.copy2(wheels[-1], target)
        return str(target)
    finally:
        shutil.rmtree(tmp_out, ignore_errors=True)


# ---------------------------------------------------------------------------
# render helpers
# ---------------------------------------------------------------------------

def _render_schemas(proposal: ApiProposal) -> str:
    return (
        '"""Generated request/response schemas."""\n'
        "from pydantic import BaseModel, Field\n\n\n"
        "class ApiRequest(BaseModel):\n"
        "    payload: dict = Field(default_factory=dict, description=\"request payload\")\n\n\n"
        "class ApiResponse(BaseModel):\n"
        "    result: dict = Field(default_factory=dict, description=\"response payload\")\n"
    )


def _render_adapter(import_module: str, func_name: str, params: list[tuple[str, str | None]]) -> str:
    # Single-arg functions (e.g. validators like validate_order(order: dict))
    # receive the whole payload; multi-arg functions map payload fields by name.
    if len(params) == 1:
        body = f"        {params[0][0]}=p,"
    else:
        call_args = []
        for name, default in params:
            if default is None:
                call_args.append(f'        {name}=p["{name}"],')
            else:
                call_args.append(f'        {name}=p.get("{name}", {default}),')
        body = "\n".join(call_args) or "        pass"
    return (
        '"""Adapter: maps the stable API payload to the legacy function."""\n'
        f"from {import_module} import {func_name}\n\n"
        "from .schemas import ApiRequest, ApiResponse\n\n\n"
        "def invoke(request: ApiRequest) -> ApiResponse:\n"
        "    p = request.payload\n"
        f"    result = {func_name}(\n"
        f"{body}\n"
        "    )\n"
        "    if result is None:\n"
        '        result = {"valid": True}\n'
        "    return ApiResponse(result=result)\n"
    )


def _render_main(proposal: ApiProposal, version: str, chash: str, ihash: str, commit: str) -> str:
    method = proposal.method.lower()
    return (
        '"""Generated FastAPI service."""\n'
        "from fastapi import FastAPI\n"
        "from .schemas import ApiRequest, ApiResponse\n"
        "from .adapter import invoke\n\n"
        f'app = FastAPI(title="{proposal.api_name}", version="{version}")\n\n'
        "SOURCE_REPOSITORY = " + repr(proposal.module_id.split(":", 1)[0]) + "\n"
        "SOURCE_COMMIT = " + repr(commit) + "\n"
        f'CONTRACT_HASH = "{chash}"\n'
        f'IMPLEMENTATION_HASH = "{ihash}"\n\n\n'
        '@app.get("/health")\n'
        "def health():\n"
        '    return {"status": "ok"}\n\n\n'
        '@app.get("/metadata")\n'
        "def metadata():\n"
        "    return {\n"
        f'        "asset_id": "{proposal.api_name}",\n'
        f'        "version": "{version}",\n'
        '        "source_repository": SOURCE_REPOSITORY,\n'
        '        "source_commit": SOURCE_COMMIT,\n'
        '        "contract_hash": CONTRACT_HASH,\n'
        '        "implementation_hash": IMPLEMENTATION_HASH,\n'
        "    }\n\n\n"
        f'@app.{method}("{proposal.path}")\n'
        "def business_endpoint(request: ApiRequest) -> ApiResponse:\n"
        "    return invoke(request)\n"
    )


def _render_contract(
    proposal: ApiProposal, import_module: str, func_name: str, signature: str,
    params: list[tuple[str, str | None]], version: str,
) -> str:
    doc = {
        "asset_id": proposal.api_name,
        "kind": "http-api",
        "version": version,
        "capability": proposal.capability,
        "entry": {
            "module": import_module,
            "function": func_name,
            "signature": signature,
            "params": [
                {"name": name, "required": default is None, "default": default}
                for name, default in params
            ],
        },
        "endpoint": {"method": proposal.method, "path": proposal.path},
        "request_schema": proposal.request_schema,
        "response_schema": proposal.response_schema,
        "source": {
            "repository": proposal.module_id.split(":", 1)[0],
            "paths": proposal.source_paths,
        },
    }
    return _json_dumps(doc)


def _render_manifest(proposal: ApiProposal, version: str, chash: str, ihash: str, commit: str) -> str:
    repo = proposal.module_id.split(":", 1)[0]
    doc = {
        "asset_id": proposal.api_name,
        "name": proposal.capability,
        "kind": "http-api",
        "version": version,
        "source": {
            "repository": repo,
            "ref": "main",
            "commit": commit,
            "paths": proposal.source_paths,
            "symbols": proposal.entry_symbols,
        },
        "contract": {"type": "openapi", "path": "openapi.json", "hash": chash},
        "implementation": {"hash": ihash},
        "generation": {"template_version": TEMPLATE_VERSION, "llm_used": False},
        "verification": {"status": "unverified"},
    }
    return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)


def _render_provenance(
    proposal: ApiProposal, version: str, commit: str, blob_shas: list[str],
    closure_files: dict[str, str],
) -> str:
    doc = {
        "asset_id": proposal.api_name,
        "version": version,
        "source_commit": commit,
        "source_paths": proposal.source_paths,
        "entry_symbols": proposal.entry_symbols,
        "blob_shas": sorted(blob_shas),
        "dependency_closure": sorted(closure_files.keys()),
    }
    return json.dumps(doc, indent=2, ensure_ascii=False)


def _render_pyproject(proposal: ApiProposal, version: str, packages: list[str]) -> str:
    pkg_list = ", ".join(repr(p) for p in packages)
    name = proposal.api_name.replace("_", "-")
    return (
        "[build-system]\n"
        "requires = [\"setuptools>=68\"]\n"
        "build-backend = \"setuptools.build_meta\"\n\n"
        "[project]\n"
        f'name = "{name}"\n'
        f'version = "{version}"\n'
        'description = "Generated HTTP API asset"\n'
        'dependencies = ["fastapi>=0.141", "uvicorn>=0.52", "pydantic>=2.11"]\n\n'
        "[tool.setuptools]\n"
        f"packages = [{pkg_list}]\n\n"
        "[tool.setuptools.package-data]\n"
        'app = ["contract/*.json"]\n'
    )
