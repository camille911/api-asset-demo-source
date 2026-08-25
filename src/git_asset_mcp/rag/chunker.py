"""API-level chunking for RAG.

Each contract (one public function / API) becomes a small set of chunks:
  - chunk 0: API summary (name, module, signature, provenance)
  - chunk 1+: docstring detail (when non-empty)

Every chunk stays tied to its contract via ``contract_id`` (traceability:
chunk -> contract -> artifact -> wheel -> source blob).
"""
from __future__ import annotations


def _params_from_signature(signature: str) -> list[str]:
    start = signature.find("(")
    end = signature.rfind(")")
    if start < 0 or end < start:
        return []
    body = signature[start + 1:end]
    return [p.strip() for p in body.split(",") if p.strip() and p.strip() != "self"]


def _summary_chunk(contract: dict) -> str:
    parts = [
        f"API: {contract['api_name']}",
        f"Module: {contract['module_name']}",
        f"Symbol: {contract['symbol_qname']}",
        f"Signature: {contract['signature'] or '(no signature)'}",
        f"Source: {contract['path']}",
        f"Language: {contract['language']}",
    ]
    if contract.get("docstring"):
        first_line = contract["docstring"].strip().splitlines()
        if first_line:
            parts.append(f"Description: {first_line[0].strip()}")
    if contract.get("wheel_path"):
        parts.append(f"Wheel: {contract['wheel_path']}")
    if contract.get("contract_hash"):
        parts.append(f"ContractHash: {contract['contract_hash'][:12]}")
    return "\n".join(parts)


def _docstring_chunks(contract: dict, limit: int = 600) -> list[str]:
    doc = (contract.get("docstring") or "").strip()
    if not doc:
        return []
    chunks: list[str] = []
    remaining = doc
    idx = 1
    while remaining:
        piece = remaining[:limit]
        chunks.append(
            f"API detail ({contract['api_name']}) [{idx}]:\n{piece}"
        )
        remaining = remaining[limit:]
        idx += 1
    return chunks


def chunk_contract(contract: dict) -> list[tuple[int, str]]:
    """Return ``[(chunk_index, content), ...]`` for a single contract."""
    out = [(0, _summary_chunk(contract))]
    for i, text in enumerate(_docstring_chunks(contract), start=1):
        out.append((i, text))
    return out
