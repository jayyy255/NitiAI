"""Text embeddings. fastembed (ONNX, CPU) in production; a hashing embedder for offline tests."""
from __future__ import annotations

import hashlib
import re
from functools import lru_cache

import numpy as np

from .config import get_settings

_TOKEN = re.compile(r"[a-z0-9+#.]+")


class HashEmbedder:
    """Deterministic bag-of-words hashing embedder. No downloads, no semantics beyond token overlap."""

    def __init__(self, dim: int):
        self.dim = dim

    def embed(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, t in enumerate(texts):
            for tok in _TOKEN.findall((t or "").lower()):
                h = int.from_bytes(hashlib.blake2b(tok.encode(), digest_size=8).digest(), "little")
                out[i, h % self.dim] += 1.0 if (h >> 63) & 1 else -1.0
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return out / norms


class FastEmbedder:
    def __init__(self, model: str, cache_dir: str):
        from fastembed import TextEmbedding

        self._m = TextEmbedding(model_name=model, cache_dir=cache_dir)

    def embed(self, texts: list[str]) -> np.ndarray:
        vecs = np.array(list(self._m.embed([t or "" for t in texts], batch_size=32)), dtype=np.float32)
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return vecs / norms


@lru_cache(maxsize=1)
def get_embedder():
    s = get_settings()
    if s.embedder == "hash":
        return HashEmbedder(s.embedding_dim)
    return FastEmbedder(s.embedding_model, s.model_cache_dir)


def embed(texts: list[str]) -> np.ndarray:
    return get_embedder().embed(texts)
