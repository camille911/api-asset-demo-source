"""API proposal MCP tools (create / approve)."""
from __future__ import annotations

import datetime
from typing import Any

from git_asset_mcp.app import AppContext
from git_asset_mcp.proposal.proposer import propose_api
from git_asset_mcp.proposal.schemas import ApiProposal, ProposalApproval


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def register_proposal_tools(mcp: Any, ctx: AppContext) -> None:
    @mcp.tool()
    def api_proposal_create(
        repo_id: str,
        module_name: str,
        target_capability: str = "",
        entry_symbol: str = "",
    ) -> ApiProposal:
        """Create an API packaging proposal for a module.

        ``entry_symbol`` optionally selects which public function becomes the
        API entry (exact or leaf-suffix match, e.g. ``mask_sensitive_fields``).
        Without it, the first public function of the module is used. The
        proposal starts in ``proposed`` state and must be explicitly approved
        before packaging. Only public entry symbols are considered.
        """
        commit = ctx.db.get_last_scanned_commit(repo_id)
        if not commit:
            raise ValueError(f"repository {repo_id!r} has not been scanned yet")

        proposal = propose_api(ctx.db, repo_id, commit, module_name, target_capability, entry_symbol)
        ctx.db.insert_proposal(
            proposal_id=proposal.proposal_id,
            module_id=proposal.module_id,
            proposal_json=proposal.model_dump_json(),
            status=proposal.status,
            created_at=_now(),
        )
        return proposal

    @mcp.tool()
    def api_proposal_approve(
        proposal_id: str,
        approved: bool,
        approval_note: str = "",
    ) -> ProposalApproval:
        """Approve or reject a proposal.

        Only approved proposals can be packaged. Returns the new status and
        timestamp.
        """
        record = ctx.db.get_proposal(proposal_id)
        if not record:
            raise ValueError(f"proposal {proposal_id!r} not found")

        new_status = "approved" if approved else "rejected"
        ctx.db.update_proposal_status(proposal_id, new_status)
        return ProposalApproval(
            proposal_id=proposal_id,
            approved=approved,
            status=new_status,
            approved_at=_now(),
        )
