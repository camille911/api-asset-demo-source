"""Language analyzer registry.

Each analyzer lives in its own subpackage (``python`` / ``cpp`` / ...) and
exposes a single ``analyze_source(source: str, path: str) -> FileAnalysis``
entry point. The scan pipeline classifies a repository file by name and
dispatches to the matching analyzer here, so adding a language only means:
  1. add a subpackage with ``analyze_source``,
  2. register its extensions / filenames below.
"""
from __future__ import annotations

from git_asset_mcp.analyzers.models import FileAnalysis

#: Supported languages, in scan/report order.
LANGUAGES = ("python", "cpp", "dockerfile")

#: Lowercased file extension -> language.
_EXTENSION_MAP: dict[str, str] = {
    ".py": "python",
    # C / C++ / CUDA — one analyzer (tree-sitter-cpp grammar).
    ".c": "cpp",
    ".h": "cpp",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".c++": "cpp",
    ".hpp": "cpp",
    ".hh": "cpp",
    ".hxx": "cpp",
    ".cu": "cpp",
    ".cuh": "cpp",
}

#: Exact (lowercased) filename -> language.
_FILENAME_MAP: dict[str, str] = {
    "dockerfile": "dockerfile",
}


def classify_file(path: str) -> str | None:
    """Map a repository file path to a supported language, or ``None``.

    Handles ``Dockerfile``, ``Dockerfile.dev`` and ``*.dockerfile`` names in
    addition to the extension map. Case-insensitive.
    """
    p = path.replace("\\", "/")
    name = p.rsplit("/", 1)[-1]
    low = name.lower()

    if low in _FILENAME_MAP:
        return _FILENAME_MAP[low]
    if low.startswith("dockerfile") or low.endswith(".dockerfile"):
        return "dockerfile"

    dot = low.rfind(".")
    if dot > 0:
        return _EXTENSION_MAP.get(low[dot:])
    return None


def analyze_source(source: str, path: str) -> FileAnalysis:
    """Dispatch ``analyze_source`` to the analyzer for ``path``'s language.

    Raises ``ValueError`` when the file's language is not supported.
    """
    language = classify_file(path)
    if language is None:
        raise ValueError(f"unsupported file type: {path!r}")
    return get_analyzer(language).analyze_source(source, path)


def get_analyzer(language: str):
    """Return the analyzer module for a language (imports lazily)."""
    if language == "python":
        from git_asset_mcp.analyzers import python as _mod  # noqa: F401  (package)
        from git_asset_mcp.analyzers.python import ast_parser as _analyzer
    elif language == "cpp":
        from git_asset_mcp.analyzers.cpp import ast_parser as _analyzer
    elif language == "dockerfile":
        from git_asset_mcp.analyzers.dockerfile import parser as _analyzer
    else:
        raise ValueError(f"unknown analyzer language: {language!r}")
    return _analyzer
