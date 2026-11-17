_Run 2026-11-17 07:06 UTC · 10 profiles · 72 jobs · 105 labelled pairs · embedder `BAAI/bge-small-en-v1.5` · reranker `Xenova/ms-marco-MiniLM-L-6-v2`_

| Retrieval | Recall@20 | NDCG@10 | p50 latency |
|---|---:|---:|---:|
| BM25 (lexical) | 1.000 | 0.925 | 398 ms |
| Vector (dense embeddings) | 0.980 | 0.884 | 465 ms |
| Hybrid (BM25 + vector, RRF) | 0.980 | 0.929 | 451 ms |
| Hybrid + cross-encoder rerank + match score | 1.000 | 0.889 | 1025 ms |

| Explanation groundedness | Value |
|---|---:|
| generator | template |
| evidence quotes verified in source | 1.0 |
| claims dropped by verifier | 0 |
