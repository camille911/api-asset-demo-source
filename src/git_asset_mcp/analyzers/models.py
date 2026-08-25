"""Shared analysis data model used by every language analyzer.

Language analyzers (python / cpp / dockerfile / ...) must return a
``FileAnalysis`` with the same shape so the scan pipeline, module detector
and RAG contract extractor stay language-agnostic.

Originally defined in ``analyzers.python.ast_parser``; moved here so other
analyzers can reuse them without importing the Python-specific module.
``analyzers.python.ast_parser`` re-exports them for backward compatibility.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Symbol:
    qualified_name: str
    symbol_type: str  # module | class | function | method | namespace | instruction | ...
    signature: str
    start_line: int
    end_line: int
    docstring: str = ""
    decorators: list[str] = field(default_factory=list)
    params: list[str] = field(default_factory=list)
    return_annotation: str = ""


@dataclass
class ImportEdge:
    module: str
    names: list[str] = field(default_factory=list)
    line: int = 0


@dataclass
class CallEdge:
    caller: str
    callee: str
    line: int = 0
    inferred: bool = False


@dataclass
class FileAnalysis:
    path: str
    symbols: list[Symbol] = field(default_factory=list)
    imports: list[ImportEdge] = field(default_factory=list)
    calls: list[CallEdge] = field(default_factory=list)


class ParseError(Exception):
    pass
