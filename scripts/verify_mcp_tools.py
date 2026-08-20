"""Temporary verification: run the full loop through MCP tools (not direct Python calls)."""
import asyncio
import subprocess
import tempfile
from pathlib import Path

from mcp import Client
from mcp.server import MCPServer

from git_asset_mcp.app import AppContext
from git_asset_mcp.providers.github import GithubProvider
from git_asset_mcp.settings import Settings
from git_asset_mcp.store.database import Database
from git_asset_mcp.tools.package_tools import register_package_tools
from git_asset_mcp.tools.proposal_tools import register_proposal_tools
from git_asset_mcp.tools.repository_tools import register_repository_tools
from git_asset_mcp.tools.scan_tools import register_scan_tools

CHECKOUT = '''"""checkout orchestration (business entry)."""
from legacy_checkout.pricing import subtotal_it
from legacy_checkout.discount import dsc
from legacy_checkout.shipping import fee


def calculate_checkout(items, customer_tier="regular", region="east", coupon_code=None, currency="CNY"):
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

PRICING = '''def subtotal_it(items):
    total = 0.0
    for it in items:
        total += float(it.get("unit_price", "0")) * int(it.get("quantity", 0))
    return round(total, 2)
'''

DISCOUNT = '''TIER_RATES = {"regular": 0.0, "silver": 0.05, "gold": 0.10}


def dsc(subtotal, customer_tier, coupon_code=None):
    rate = TIER_RATES.get(customer_tier, 0.0)
    if coupon_code == "SAVE10":
        rate += 0.10
    return round(subtotal * rate, 2)
'''

SHIPPING = '''BASE_FEE = 5.0
REGION_FACTOR = {"east": 1.0, "north": 1.2, "south": 1.5}


def fee(region, weight_kg):
    return round(BASE_FEE * REGION_FACTOR.get(region, 1.0) + float(weight_kg) * 2.0, 2)
'''


def _git(args, cwd):
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="mcp-tools-verify-"))
    repo = tmp / "source"
    repo.mkdir()
    _git(["init", "-q", "-b", "main"], repo)
    _git(["config", "user.email", "t@e.com"], repo)
    _git(["config", "user.name", "t"], repo)
    pkg = repo / "src" / "legacy_checkout"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text('"""pkg"""\n')
    (pkg / "checkout.py").write_text(CHECKOUT)
    (pkg / "pricing.py").write_text(PRICING)
    (pkg / "discount.py").write_text(DISCOUNT)
    (pkg / "shipping.py").write_text(SHIPPING)
    _git(["add", "."], repo)
    _git(["commit", "-q", "-m", "init"], repo)

    settings = Settings(data_dir=tmp / "data", generated_dir=tmp / "generated")
    provider = GithubProvider(data_dir=settings.data_dir, allow_local_paths=True)
    db = Database(settings.data_dir / "metadata.db")
    ctx = AppContext(settings=settings, provider=provider, db=db)

    mcp = MCPServer("test")
    register_repository_tools(mcp, ctx)
    register_scan_tools(mcp, ctx)
    register_proposal_tools(mcp, ctx)
    register_package_tools(mcp, ctx)

    async def run():
        async with Client(mcp) as c:
            reg = await c.call_tool("repository_register", {"repository_url": str(repo), "ref": "main"})
            data = reg.structured_content
            repo_id = data["repo_id"]
            print(f"[register] repo_id={repo_id} commit={data['resolved_commit'][:8]}")

            scan = await c.call_tool("repository_scan", {"repo_id": repo_id})
            s = scan.structured_content
            print(f"[scan] symbols={s['symbols_total']} modules={[m['name'] for m in s['modules']]}")

            ml = await c.call_tool("module_list", {"repo_id": repo_id})
            print(f"[module_list] {[m['name'] for m in ml.structured_content['modules']]}")

            prop = await c.call_tool("api_proposal_create", {
                "repo_id": repo_id, "module_name": "legacy_checkout.checkout",
                "target_capability": "checkout_quote",
            })
            pid = prop.structured_content["proposal_id"]
            print(f"[proposal] id={pid[:8]} api={prop.structured_content['api_name']} status={prop.structured_content['status']}")

            await c.call_tool("api_proposal_approve", {"proposal_id": pid, "approved": True})

            b = await c.call_tool("api_package_build", {"proposal_id": pid, "version": "1.0.0"})
            bd = b.structured_content
            print(f"[build] wheel={Path(bd['wheel_path']).name} contract={bd['contract_hash'][:12]}")
            artifact_id = bd["artifact_id"]

            v = await c.call_tool("api_package_verify", {"artifact_id": artifact_id})
            print(f"[verify] status={v.structured_content['status']}")

            # 修改源码 -> 增量
            (repo / "src/legacy_checkout/checkout.py").write_text(
                CHECKOUT.replace("total = round(subtotal - discount + shipping_fee, 2)",
                                 "total = round((subtotal - discount) + shipping_fee, 2)")
            )
            _git(["add", "."], repo)
            _git(["commit", "-q", "-m", "refactor"], repo)

            uc = await c.call_tool("repository_update_check", {"repo_id": repo_id})
            print(f"[update_check] has_changes={uc.structured_content['has_changes']} changed={uc.structured_content['changed_files']}")

            # 重新扫描新 commit，让 update_plan 能对比 blob sha
            await c.call_tool("repository_scan", {"repo_id": repo_id})

            up = await c.call_tool("api_update_plan", {"artifact_id": artifact_id})
            print(f"[update_plan] compatibility={up.structured_content['compatibility']} recommended={up.structured_content['recommended_version']}")
        return 0

    return asyncio.run(run())


if __name__ == "__main__":
    raise SystemExit(main())
