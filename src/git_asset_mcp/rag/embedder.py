"""Semantic embedder backed by sentence-transformers.

Loads a local embedding model lazily (first call only) and caches it for
the process lifetime. Model name is configurable via the ``RAG_EMBED_MODEL``
environment variable; defaults to ``nomic-embed-text-v1.5``.
"""
from __future__ import annotations

import os
from functools import lru_cache

import numpy as np

DEFAULT_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

_LOCAL_MODELS_DIR = os.environ.get(
    "RAG_MODELS_DIR",
    os.path.join(os.path.expanduser("~"), ".git-asset", "models"),
)

# 进程内单例缓存：模型只加载一次，后续检索复用内存实例。
_EMBEDDER_CACHE: dict[str, "Embedder"] = {}


def get_embedder(model_name: str | None = None) -> "Embedder":
    """Return a process-wide cached Embedder (single model load)."""
    key = model_name or os.environ.get("RAG_EMBED_MODEL", DEFAULT_MODEL)
    if key not in _EMBEDDER_CACHE:
        _EMBEDDER_CACHE[key] = Embedder(model_name=key)
    return _EMBEDDER_CACHE[key]


def _resolve_model_path(model_name: str) -> str:
    """Resolve a model name to a local path when it exists locally.

    Supports both ``name`` (HF repo id, downloaded via huggingface_hub) and
    ``name`` found under ``~/.git-asset/models`` (manually downloaded).
    """
    if model_name.startswith((".", "/", "C:", "\\")):
        return model_name
    local = os.path.join(_LOCAL_MODELS_DIR, model_name.split("/")[-1])
    if os.path.isdir(local):
        return local
    return model_name


class Embedder:
    """Minimal wrapper around SentenceTransformer with lazy loading."""

    def __init__(self, model_name: str | None = None):
        self._model_name = model_name or os.environ.get("RAG_EMBED_MODEL", DEFAULT_MODEL)
        self._model = None

    def _ensure(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            target = _resolve_model_path(self._model_name)
            self._model = SentenceTransformer(target, trust_remote_code=True)
        return self._model

    def embed(self, texts: list[str]) -> np.ndarray:
        """Return an ``(n, dim)`` float32 matrix for ``texts``."""
        if not texts:
            return np.zeros((0, 0), dtype=np.float32)
        model = self._ensure()
        vecs = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        return np.asarray(vecs, dtype=np.float32)

    @property
    def model_name(self) -> str:
        return self._model_name
