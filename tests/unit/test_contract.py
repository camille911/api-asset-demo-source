"""Unit tests for contract and implementation hashing."""
from git_asset_mcp.packagers.fastapi.contract import contract_hash, implementation_hash


def test_contract_hash_stable():
    openapi = {
        "paths": {"/v1/x": {"post": {}}},
        "components": {"schemas": {"A": {"type": "object"}}},
    }
    assert contract_hash(openapi) == contract_hash(openapi)
    assert len(contract_hash(openapi)) == 64


def test_contract_hash_ignores_info_fields():
    a = {
        "info": {"title": "X", "version": "1"},
        "paths": {"/v1/x": {"post": {}}},
        "components": {"schemas": {}},
    }
    b = {
        "info": {"title": "DIFFERENT", "version": "999"},
        "paths": {"/v1/x": {"post": {}}},
        "components": {"schemas": {}},
    }
    assert contract_hash(a) == contract_hash(b)


def test_contract_hash_changes_with_path():
    a = {"paths": {"/v1/x": {"post": {}}}, "components": {"schemas": {}}}
    b = {"paths": {"/v1/y": {"post": {}}}, "components": {"schemas": {}}}
    assert contract_hash(a) != contract_hash(b)


def test_implementation_hash_changes_with_source():
    h1 = implementation_hash(["abc123"], "adapter v1", "0.1.0")
    h2 = implementation_hash(["def456"], "adapter v1", "0.1.0")
    h3 = implementation_hash(["abc123"], "adapter v2", "0.1.0")
    assert h1 != h2
    assert h1 != h3
