"""Hybrid retrieval: Postgres full-text (ts_rank_cd) + pgvector cosine, fused with RRF,
then cross-encoder reranking blended with the explainable match score.

Two interchangeable backends share one interface, so the same ranking code runs against
Postgres in production and fully in memory in the eval harness and CI.
"""
from __future__ import annotations

import re
import time
from typing import Protocol

import numpy as np

from .config import get_settings
from .embeddings import embed
from .models import Job, Profile, ScoredJob
from .rerank import rerank_scores
from .scoring import score_job

_TERM = re.compile(r"[a-z0-9]+")
_STOP = {"and", "or", "the", "a", "an", "of", "for", "in", "at", "to", "with", "on", "is", "are", "be",
         "we", "you", "our", "your", "will", "skills", "experience", "years", "work", "team"}


def query_terms(text: str, limit: int = 40) -> list[str]:
    seen: dict[str, None] = {}
    for t in _TERM.findall((text or "").lower()):
        if t not in _STOP and len(t) > 1:
            seen.setdefault(t, None)
    return list(seen)[:limit]


def keyword_query(profile: Profile, query: str | None) -> str:
    if query:
        return query
    return " ".join([profile.title or "", " ".join(profile.preferred_roles), " ".join(profile.skills)])


def semantic_query(profile: Profile, query: str | None) -> str:
    # an explicit query is embedded on its own; mixing in the profile drowns it out
    return query or profile.as_text()


class Backend(Protocol):
    def bm25(self, text: str, k: int, location: str | None, remote_only: bool) -> list[tuple[str, float]]: ...
    def vector(self, vec: np.ndarray, k: int, location: str | None, remote_only: bool) -> list[tuple[str, float]]: ...
    def get_jobs(self, ids: list[str]) -> dict[str, Job]: ...


def rrf(rankings: list[list[tuple[str, float]]], k: int) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion: Σ 1 / (k + rank)."""
    fused: dict[str, float] = {}
    for ranking in rankings:
        for rank, (doc_id, _) in enumerate(ranking, start=1):
            fused[doc_id] = fused.get(doc_id, 0.0) + 1.0 / (k + rank)
    return sorted(fused.items(), key=lambda x: -x[1])


# ---------------------------------------------------------------- Postgres

JOB_COLS = ("id::text AS id, title, company, location, remote, description, url, salary_text, salary_min, "
            "salary_max, skills, min_years, posted_at, source")


def row_to_job(r: dict) -> Job:
    num = lambda v: float(v) if v is not None else None  # noqa: E731
    return Job(id=r["id"], title=r["title"], company=r.get("company"), location=r.get("location"),
               remote=bool(r.get("remote")), description=r.get("description") or "", url=r.get("url"),
               salary_text=r.get("salary_text"), salary_min=num(r.get("salary_min")),
               salary_max=num(r.get("salary_max")), skills=list(r.get("skills") or []),
               min_years=num(r.get("min_years")), posted_at=r.get("posted_at"), source=r.get("source"))


class PostgresBackend:
    @staticmethod
    def _filters(location: str | None, remote_only: bool) -> tuple[str, list]:
        where, params = ["is_active"], []
        if remote_only:
            where.append("remote")
        if location:
            where.append("(remote OR location ILIKE %s)")
            params.append(f"%{location.split(',')[0].strip()}%")
        return " AND ".join(where), params

    def bm25(self, text, k, location=None, remote_only=False):
        from .db import conn

        terms = query_terms(text)
        if not terms:
            return []
        where, params = self._filters(location, remote_only)
        sql = ("SELECT id::text AS id, ts_rank_cd(search_tsv, q, 32) AS s "
               "FROM jobs, to_tsquery('english', %s) q "
               f"WHERE search_tsv @@ q AND {where} ORDER BY s DESC LIMIT %s")
        with conn() as c:
            rows = c.execute(sql, [" | ".join(terms), *params, k]).fetchall()
        return [(r["id"], float(r["s"])) for r in rows]

    def vector(self, vec, k, location=None, remote_only=False):
        from .db import conn

        where, params = self._filters(location, remote_only)
        sql = ("SELECT id::text AS id, 1 - (embedding <=> %s) AS s FROM jobs "
               f"WHERE embedding IS NOT NULL AND {where} ORDER BY embedding <=> %s LIMIT %s")
        v = np.asarray(vec, dtype=np.float32)
        with conn() as c:
            rows = c.execute(sql, [v, *params, v, k]).fetchall()
        return [(r["id"], float(r["s"])) for r in rows]

    def get_jobs(self, ids):
        from .db import conn

        if not ids:
            return {}
        with conn() as c:
            rows = c.execute(f"SELECT {JOB_COLS} FROM jobs WHERE id::text = ANY(%s)", [ids]).fetchall()
        return {r["id"]: row_to_job(r) for r in rows}


# ---------------------------------------------------------------- in-memory

class MemoryBackend:
    """BM25Okapi + numpy cosine over a fixed list of jobs (eval harness, tests)."""

    def __init__(self, jobs: list[Job]):
        from rank_bm25 import BM25Okapi

        self.jobs = {j.id: j for j in jobs}
        self.ids = [j.id for j in jobs]
        docs = [query_terms(f"{j.title} {j.title} {' '.join(j.skills)} {j.description}", limit=100_000) for j in jobs]
        self._bm25 = BM25Okapi(docs)
        self._vecs = embed([j.as_text() for j in jobs])

    def _ok(self, j: Job, location, remote_only):
        if remote_only and not j.remote:
            return False
        if location and not j.remote and location.split(",")[0].strip().lower() not in (j.location or "").lower():
            return False
        return True

    def bm25(self, text, k, location=None, remote_only=False):
        scores = self._bm25.get_scores(query_terms(text))
        order = np.argsort(-scores)
        out = [(self.ids[i], float(scores[i])) for i in order
               if scores[i] > 0 and self._ok(self.jobs[self.ids[i]], location, remote_only)]
        return out[:k]

    def vector(self, vec, k, location=None, remote_only=False):
        sims = self._vecs @ vec
        order = np.argsort(-sims)
        out = [(self.ids[i], float(sims[i])) for i in order if self._ok(self.jobs[self.ids[i]], location, remote_only)]
        return out[:k]

    def get_jobs(self, ids):
        return {i: self.jobs[i] for i in ids if i in self.jobs}


# ---------------------------------------------------------------- pipeline

def search(backend: Backend, profile: Profile | None, query: str | None = None, k: int = 20,
           mode: str = "hybrid_rerank", location: str | None = None, remote_only: bool = False,
           timings: dict | None = None) -> list[ScoredJob]:
    s = get_settings()
    profile = profile or Profile()
    timings = timings if timings is not None else {}
    pool_n = max(s.candidate_pool, k)

    t0 = time.perf_counter()
    kw_text = keyword_query(profile, query)
    sem_text = semantic_query(profile, query)
    qvec = embed([sem_text])[0]
    timings["embed_ms"] = round((time.perf_counter() - t0) * 1000, 1)

    t0 = time.perf_counter()
    lex = backend.bm25(kw_text, pool_n, location, remote_only) if mode != "vector" else []
    vec = backend.vector(qvec, pool_n, location, remote_only) if mode != "bm25" else []
    timings["retrieve_ms"] = round((time.perf_counter() - t0) * 1000, 1)

    lex_rank = {d: i + 1 for i, (d, _) in enumerate(lex)}
    vec_rank = {d: i + 1 for i, (d, _) in enumerate(vec)}
    if mode == "bm25":
        ranked = lex
    elif mode == "vector":
        ranked = vec
    else:
        ranked = rrf([lex, vec], s.rrf_k)
    ranked = ranked[: max(s.rerank_top_n, k)] if mode == "hybrid_rerank" else ranked[:k]

    ids = [d for d, _ in ranked]
    jobs = backend.get_jobs(ids)
    ids = [d for d in ids if d in jobs]

    # An explicit query states what the user wants right now: it drives semantic relevance and the
    # role-preference component; the profile still supplies skills, experience and location.
    scoring_profile = profile.model_copy(update={"preferred_roles": [query], "title": None}) if query else profile
    rerank_text = query if query else sem_text

    t0 = time.perf_counter()
    if not ids:
        sem: list[float] = []
    elif mode == "hybrid_rerank":
        sem = rerank_scores(rerank_text, [jobs[d].as_text() for d in ids])
    else:
        # cheap bi-encoder relevance for the score shown on non-reranked modes
        jv = embed([jobs[d].as_text() for d in ids])
        sem = [float((x + 1) / 2) for x in (jv @ qvec)]
    timings["rerank_ms"] = round((time.perf_counter() - t0) * 1000, 1)

    fused = dict(ranked)
    results = []
    for d, sm in zip(ids, sem):
        results.append(ScoredJob(job=jobs[d], match=score_job(scoring_profile, jobs[d], sm), retrieval={
            "bm25_rank": lex_rank.get(d), "vector_rank": vec_rank.get(d),
            "rrf": round(fused.get(d, 0.0), 5), "semantic": round(sm, 4)}))
    if mode == "hybrid_rerank":
        if query:
            # explicit search: relevance to the query leads, personal fit breaks ties
            results.sort(key=lambda r: -(0.6 * r.retrieval["semantic"] + 0.4 * r.match.score / 100))
        else:
            # recommendations: blended match score (cross-encoder + skills + location + prefs + experience)
            results.sort(key=lambda r: (-r.match.score, -(r.retrieval["rrf"] or 0)))
    return results[:k]
