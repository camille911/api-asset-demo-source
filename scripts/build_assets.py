#!/usr/bin/env python3
"""Build the three-part wheels locally (no remote GitHub, no persistent artifacts).

Runs: local git fixture -> scan -> propose -> approve -> build 1.0.0 (wheel)
      -> modify source -> update plan -> build 1.0.1 (wheel) -> verify business call.

Wheels are copied into <project>/dist/. All intermediate state lives in OS-temp
directories and is auto-cleaned.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from git_asset_mcp.analyzers.python.inventory import scan_repository
from git_asset_mcp.packagers.fastapi.generator import build_artifact
from git_asset_mcp.packagers.fastapi.verifier import verify_artifact
from git_asset_mcp.proposal.proposer import propose_api
from git_asset_mcp.providers.github import GithubProvider
from git_asset_mcp.store.database import Database
from git_asset_mcp.updater import update_check, update_plan

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DIST_DIR = PROJECT_ROOT / "dist"

INIT_PY = '"""legacy_checkout package."""\n'

PRICING = '''"""pricing helpers (legacy, naming unclear)."""


def subtotal_it(items):
    total = 0.0
    for it in items:
        total += float(it.get("unit_price", "0")) * int(it.get("quantity", 0))
    return round(total, 2)
'''

DISCOUNT = '''"""discount rules (legacy)."""

TIER_RATES = {"regular": 0.0, "silver": 0.05, "gold": 0.10}


def dsc(subtotal, customer_tier, coupon_code=None):
    rate = TIER_RATES.get(customer_tier, 0.0)
    if coupon_code == "SAVE10":
        rate += 0.10
    return round(subtotal * rate, 2)
'''

SHIPPING = '''"""shipping fee (legacy)."""

BASE_FEE = 5.0
REGION_FACTOR = {"east": 1.0, "north": 1.2, "south": 1.5}


def fee(region, weight_kg):
    return round(BASE_FEE * REGION_FACTOR.get(region, 1.0) + float(weight_kg) * 2.0, 2)
'''

CHECKOUT_V1 = '''"""checkout orchestration (business entry)."""
from legacy_checkout.pricing import subtotal_it
from legacy_checkout.discount import dsc
from legacy_checkout.shipping import fee


def calculate_checkout(items, customer_tier="regular", region="east", coupon_code=None, currency="CNY"):
    """Generate a checkout quote from items, tier, region, and optional coupon."""
    subtotal = subtotal_it(items)
    discount = dsc(subtotal, customer_tier, coupon_code)
    weight = sum(float(i.get("weight_kg", "0")) for i in items)
    shipping_fee = fee(region, weight)
    total = round(subtotal - discount + shipping_fee, 2)
    return {
        "subtotal": f"{subtotal:.2f}",
        "discount": f"{discount:.2f}",
        "shipping_fee": f"{shipping_fee:.2f}",
        "total": f"{total:.2f}",
        "currency": currency,
    }
'''

CHECKOUT_V2 = '''"""checkout orchestration (business entry)."""
from legacy_checkout.pricing import subtotal_it
from legacy_checkout.discount import dsc
from legacy_checkout.shipping import fee


def calculate_checkout(items, customer_tier="regular", region="east", coupon_code=None, currency="CNY"):
    """Generate a checkout quote from items, tier, region, and optional coupon."""
    subtotal = subtotal_it(items)
    discount = dsc(subtotal, customer_tier, coupon_code)
    weight = sum(float(i.get("weight_kg", "0")) for i in items)
    shipping_fee = fee(region, weight)
    price_after_discount = subtotal - discount
    total = round(price_after_discount + shipping_fee, 2)
    return {
        "subtotal": f"{subtotal:.2f}",
        "discount": f"{discount:.2f}",
        "shipping_fee": f"{shipping_fee:.2f}",
        "total": f"{total:.2f}",
        "currency": currency,
    }
'''

FILES = {
    "src/legacy_checkout/__init__.py": INIT_PY,
    "src/legacy_checkout/pricing.py": PRICING,
    "src/legacy_checkout/discount.py": DISCOUNT,
    "src/legacy_checkout/shipping.py": SHIPPING,
    "src/legacy_checkout/checkout.py": CHECKOUT_V1,
    "pyproject.toml": "[project]\nname = 'legacy-checkout'\nversion = '0.1.0'\n",
}

EXAMPLE = {
    "items": [{"sku": "SKU-001", "unit_price": "100.00", "quantity": 2, "weight_kg": "1.50"}],
    "customer_tier": "gold",
    "region": "east",
}


def _git(args, cwd):
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="build-assets-"))
    repo = work / "source"
    repo.mkdir()
    _git(["init", "-q", "-b", "main"], repo)
    _git(["config", "user.email", "demo@example.com"], repo)
    _git(["config", "user.name", "demo"], repo)
    for path, content in FILES.items():
        f = repo / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content)
    _git(["add", "."], repo)
    _git(["commit", "-q", "-m", "init"], repo)

    provider = GithubProvider(data_dir=work / "data", allow_local_paths=True)
    ref = provider.register(str(repo), "main")
    db = Database(work / "data" / "metadata.db")
    generated = work / "generated"

    # 1.0.0
    scan = scan_repository(provider, db, ref.repo_id, ref.resolved_commit)
    print(f"[scan] commit={ref.resolved_commit[:8]} symbols={scan['symbols_total']}")
    p1 = propose_api(db, ref.repo_id, ref.resolved_commit,
                     "legacy_checkout.checkout", target_capability="checkout_quote")
    db.insert_proposal(p1.proposal_id, p1.module_id, p1.model_dump_json(), "approved", "now")
    p1.status = "approved"
    b100 = build_artifact(p1, provider, db, "1.0.0", generated, build_wheel=True)
    print(f"[build 1.0.0] {b100['contract_hash'][:12]} wheel={b100['wheel_path']}")
    _copy_wheel(b100["wheel_path"])
    v100 = verify_artifact(generated / p1.api_name / "1.0.0", example_request=EXAMPLE)
    print(f"[verify 1.0.0] {v100['status']} golden={v100.get('golden_vector')}")

    # modify source -> 1.0.1
    (repo / "src/legacy_checkout/checkout.py").write_text(CHECKOUT_V2)
    _git(["add", "."], repo)
    _git(["commit", "-q", "-m", "refactor total calc"], repo)

    check = update_check(provider, db, ref.repo_id, "main")
    scan_repository(provider, db, ref.repo_id, check["new_commit"])
    plan = update_plan(provider, db, b100["artifact_id"])
    print(f"[update_plan] {plan['compatibility']} recommended={plan['recommended_version']} "
          f"impl={plan['implementation_changed']} contract={plan['contract_changed']}")

    p2 = propose_api(db, ref.repo_id, check["new_commit"],
                     "legacy_checkout.checkout", target_capability="checkout_quote")
    p2.status = "approved"
    b101 = build_artifact(p2, provider, db, "1.0.1", generated, build_wheel=True)
    print(f"[build 1.0.1] {b101['contract_hash'][:12]} wheel={b101['wheel_path']}")
    _copy_wheel(b101["wheel_path"])
    v101 = verify_artifact(generated / p2.api_name / "1.0.1", example_request=EXAMPLE)
    print(f"[verify 1.0.1] {v101['status']} golden={v101.get('golden_vector')}")

    print("\n=== done ===")
    print("wheels in:", DIST_DIR)
    for w in sorted(DIST_DIR.glob("checkout-quote-*.whl")):
        print("  ", w.name)
    return 0


def _copy_wheel(src: str) -> None:
    if not src:
        return
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, DIST_DIR / Path(src).name)


if __name__ == "__main__":
    raise SystemExit(main())
