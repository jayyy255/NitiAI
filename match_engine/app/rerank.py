"""Cross-encoder reranking (fastembed ONNX ms-marco MiniLM). Falls back to bi-encoder cosine."""
from __future__ import annotations

import math
from functools import lru_cache

import numpy as np

from .config import get_settings
from .embeddings import embed


class _CrossEncoder:
    def __init__(self, model: str, cache_dir: str):
        from fastembed.rerank.cross_encoder import TextCrossEncoder

        self._m = TextCrossEncoder(model_name=model, cache_dir=cache_dir)

    def score(self, query: str, docs: list[str]) -> list[float]:
        raw = list(self._m.rerank(query, docs, batch_size=16))
        # ms-marco logits -> 0..1
        return [1.0 / (1.0 + math.exp(-float(x))) for x in raw]


class _CosineReranker:
    def score(self, query: str, docs: list[str]) -> list[float]:
        vecs = embed([query] + docs)
        sims = vecs[1:] @ vecs[0]
        return [float((s + 1) / 2) for s in np.clip(sims, -1, 1)]


@lru_cache(maxsize=1)
def get_reranker():
    s = get_settings()
    if s.reranker == "fastembed" and s.embedder != "hash":
        return _CrossEncoder(s.rerank_model, s.model_cache_dir)
    return _CosineReranker()


def rerank_scores(query: str, docs: list[str]) -> list[float]:
    if not docs:
        return []
    # cross-encoders are trained on short passages; keep the most informative part
    return get_reranker().score(query[:2000], [d[:2000] for d in docs])
