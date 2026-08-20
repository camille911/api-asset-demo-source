"""Pydantic schemas for API proposals (stable MCP tool contracts)."""
from __future__ import annotations

from pydantic import BaseModel, Field


class ApiProposal(BaseModel):
    """An API packaging proposal. Initial status is always ``proposed``."""

    proposal_id: str
    module_id: str
    api_name: str
    capability: str
    method: str = "POST"
    path: str
    request_schema: dict = Field(default_factory=dict)
    response_schema: dict = Field(default_factory=dict)
    error_model: dict = Field(default_factory=dict)
    source_paths: list[str] = Field(default_factory=list)
    entry_symbols: list[str] = Field(default_factory=list)
    adapter_needed: bool = True
    risks: list[str] = Field(default_factory=list)
    confidence: str = "low"
    status: str = "proposed"
    requires_confirmation: bool = True


class ProposalApproval(BaseModel):
    proposal_id: str
    approved: bool
    status: str
    approved_at: str
