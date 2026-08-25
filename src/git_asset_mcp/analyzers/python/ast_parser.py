"""Python AST analysis: symbols, imports, and call relations.

Uses the standard library ``ast`` module. AST can only prove syntactic facts,
so call edges that cannot be statically resolved are marked ``inferred=True``.
"""
from __future__ import annotations

import ast

# Data model lives in the shared analyzers.models module; re-exported here for
# backward compatibility with code importing from analyzers.python.ast_parser.
from git_asset_mcp.analyzers.models import (
    CallEdge,
    FileAnalysis,
    ImportEdge,
    ParseError,
    Symbol,
)

__all__ = [
    "Symbol",
    "ImportEdge",
    "CallEdge",
    "FileAnalysis",
    "ParseError",
]


def _module_name_from_path(path: str) -> str:
    p = path.replace("\\", "/").rstrip("/")
    if p.endswith(".py"):
        p = p[: -len(".py")]
    if p.endswith("__init__"):
        p = p[: -len("__init__")].rstrip("/")
    return p.replace("/", ".").strip(".") or "<module>"


class _Visitor(ast.NodeVisitor):
    def __init__(self, module_name: str, analysis: FileAnalysis):
        self._module = module_name
        self._analysis = analysis
        self._scope: list[str] = [module_name]

    @property
    def _current(self) -> str:
        return ".".join(self._scope)

    def _push(self, name: str) -> None:
        self._scope.append(name)

    def _pop(self) -> None:
        self._scope.pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        qname = f"{self._current}.{node.name}"
        bases = ", ".join(ast.unparse(b) for b in node.bases) or ""
        sig = f"class {node.name}({bases})" if bases else f"class {node.name}"
        self._analysis.symbols.append(
            Symbol(
                qualified_name=qname,
                symbol_type="class",
                signature=sig,
                start_line=node.lineno,
                end_line=node.end_lineno or node.lineno,
                docstring=ast.get_docstring(node, clean=False) or "",
                decorators=[ast.unparse(d) for d in node.decorator_list],
            )
        )
        self._push(node.name)
        for child in node.body:
            self.visit(child)
        self._pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._add_function(node, "function")

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._add_function(node, "async_function")

    def _add_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef, kind: str) -> None:
        qname = f"{self._current}.{node.name}"
        args = ast.unparse(node.args)
        ret = f" -> {ast.unparse(node.returns)}" if node.returns else ""
        in_class = len(self._scope) > 1
        sym_type = "method" if in_class else kind
        self._analysis.symbols.append(
            Symbol(
                qualified_name=qname,
                symbol_type=sym_type,
                signature=f"def {node.name}({args}){ret}",
                start_line=node.lineno,
                end_line=node.end_lineno or node.lineno,
                docstring=ast.get_docstring(node, clean=False) or "",
                decorators=[ast.unparse(d) for d in node.decorator_list],
                params=[a.arg for a in node.args.args],
                return_annotation=ast.unparse(node.returns) if node.returns else "",
            )
        )
        self._push(node.name)
        for child in node.body:
            self.visit(child)
        self._pop()

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._analysis.imports.append(
                ImportEdge(module=alias.name, names=[], line=node.lineno)
            )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        names = [alias.name for alias in node.names]
        self._analysis.imports.append(
            ImportEdge(module=module, names=names, line=node.lineno)
        )

    def visit_Call(self, node: ast.Call) -> None:
        callee = self._callee_name(node.func)
        if callee:
            self._analysis.calls.append(
                CallEdge(
                    caller=self._current,
                    callee=callee,
                    line=node.lineno,
                    inferred=self._is_inferred(node.func),
                )
            )
        self.generic_visit(node)

    @staticmethod
    def _callee_name(func: ast.expr) -> str:
        if isinstance(func, ast.Name):
            return func.id
        if isinstance(func, ast.Attribute):
            base = _Visitor._callee_name(func.value)
            return f"{base}.{func.attr}" if base else func.attr
        return ""

    @staticmethod
    def _is_inferred(func: ast.expr) -> bool:
        # A plain Name or Attribute is still a syntactic fact; anything dynamic
        # (call of a call, lambda, subscript) is inferred.
        return not isinstance(func, (ast.Name, ast.Attribute))


def analyze_source(source: str, path: str = "<unknown>") -> FileAnalysis:
    """Parse Python source and extract symbols, imports, and call relations."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise ParseError(f"parse failed: {exc}") from exc

    module_name = _module_name_from_path(path)
    analysis = FileAnalysis(path=path)

    module_symbol = Symbol(
        qualified_name=module_name,
        symbol_type="module",
        signature=module_name,
        start_line=1,
        end_line=len(source.splitlines()) or 1,
        docstring=ast.get_docstring(tree, clean=False) or "",
    )
    analysis.symbols.append(module_symbol)

    _Visitor(module_name, analysis).visit(tree)
    return analysis
