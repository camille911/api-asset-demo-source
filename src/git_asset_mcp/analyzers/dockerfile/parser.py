"""Dockerfile analysis via lightweight line parsing.

Dockerfile syntax is line-oriented (``INSTRUCTION args``), so no tree-sitter
grammar is needed. Each instruction is emitted as a ``Symbol`` of type
``instruction``; the module symbol carries the file itself. Continuation
lines (trailing ``\\``) are joined before emission.
"""
from __future__ import annotations

import re

from git_asset_mcp.analyzers.models import FileAnalysis, ParseError, Symbol

#: Instructions we surface as symbols (lowercased).
_INSTRUCTIONS = {
    "from", "run", "cmd", "label", "maintainer", "expose", "env", "add",
    "copy", "entrypoint", "volume", "user", "workdir", "arg", "onbuild",
    "stopsignal", "healthcheck", "shell",
}

#: Known keywords that are *not* build instructions (skip).
_NON_INSTRUCTIONS = {"syntax"}

_LEADING = re.compile(r"^[ \t]*([A-Za-z]+)[ \t]*(.*)$", re.DOTALL)


def _module_name_from_path(path: str) -> str:
    p = path.replace("\\", "/").rstrip("/")
    return p.replace("/", ".").strip(".") or "<module>"


def _collect_lines(source: str) -> list[tuple[int, str]]:
    """Return [(line_no, joined_logical_line)] with continuations merged."""
    logical: list[tuple[int, str]] = []
    buf: list[str] = []
    start = 0
    for i, raw in enumerate(source.splitlines(), start=1):
        line = raw.rstrip()
        if buf:
            buf.append(line)
        else:
            start = i
            buf = [line]
        if line.endswith("\\"):
            buf[-1] = buf[-1][:-1]  # strip trailing backslash
            continue
        logical.append((start, "\n".join(buf).strip()))
        buf = []
    if buf:
        logical.append((start, "\n".join(buf).strip()))
    return logical


def analyze_source(source: str, path: str = "<unknown>") -> FileAnalysis:
    """Parse a Dockerfile and emit one instruction symbol per logical line."""
    if not source:
        raise ParseError("empty Dockerfile")
    module_name = _module_name_from_path(path)
    analysis = FileAnalysis(path=path)
    analysis.symbols.append(
        Symbol(
            qualified_name=module_name,
            symbol_type="module",
            signature=module_name,
            start_line=1,
            end_line=len(source.splitlines()) or 1,
        )
    )

    for line_no, text in _collect_lines(source):
        if not text or text.startswith("#"):
            continue
        m = _LEADING.match(text)
        if not m:
            continue
        instr = m.group(1).lower()
        if instr in _NON_INSTRUCTIONS:
            continue
        if instr not in _INSTRUCTIONS:
            continue
        qname = f"{module_name}.{instr}#{line_no}"
        analysis.symbols.append(
            Symbol(
                qualified_name=qname,
                symbol_type="instruction",
                signature=text,
                start_line=line_no,
                end_line=line_no,
            )
        )
    return analysis
