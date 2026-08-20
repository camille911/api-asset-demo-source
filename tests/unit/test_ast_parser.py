"""Unit tests for Python AST symbol extraction."""
import pytest

from git_asset_mcp.analyzers.python.ast_parser import ParseError, analyze_source


SAMPLE = '''
"""Order validation module."""


class OrderValidationError(ValueError):
    """Raised on invalid order."""


def validate_order(order: dict) -> None:
    """Validate an order payload."""
    if not order.get("order_id"):
        raise OrderValidationError("missing order_id")
    _check_amount(order.get("amount"))


def _check_amount(amount) -> None:
    if amount is None:
        raise ValueError("amount required")
'''


def test_extract_module_symbol():
    a = analyze_source(SAMPLE, "src/order_api_b/validation.py")
    module = a.symbols[0]
    assert module.symbol_type == "module"
    assert module.qualified_name == "src.order_api_b.validation"


def test_extract_class():
    a = analyze_source(SAMPLE, "validation.py")
    classes = [s for s in a.symbols if s.symbol_type == "class"]
    assert any(s.qualified_name.endswith("OrderValidationError") for s in classes)


def test_extract_functions():
    a = analyze_source(SAMPLE, "validation.py")
    funcs = [s for s in a.symbols if s.symbol_type == "function"]
    names = {s.qualified_name for s in funcs}
    assert any(n.endswith("validate_order") for n in names)
    assert any(n.endswith("_check_amount") for n in names)


def test_signature_and_docstring():
    a = analyze_source(SAMPLE, "validation.py")
    f = next(s for s in a.symbols if s.qualified_name.endswith("validate_order"))
    assert "order: dict" in f.signature
    assert f.return_annotation == "None"
    assert "Validate an order" in f.docstring


def test_extract_calls():
    a = analyze_source(SAMPLE, "validation.py")
    callees = {c.callee for c in a.calls}
    assert "_check_amount" in callees


def test_method_symbol_type():
    src = (
        "class Checkout:\n"
        "    def run(self, items):\n"
        "        return self._total(items)\n"
        "    def _total(self, items):\n"
        "        return sum(items)\n"
    )
    a = analyze_source(src, "checkout.py")
    methods = [s for s in a.symbols if s.symbol_type == "method"]
    assert len(methods) == 2


def test_parse_error_raises():
    with pytest.raises(ParseError):
        analyze_source("def broken(:\n", "x.py")
