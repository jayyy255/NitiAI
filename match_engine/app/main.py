"""NitiAI Match Engine: FastAPI service that sits next to the Express API."""
from __future__ import annotations

import json
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel
from psycopg.types.json import Jsonb

from . import llm
from .config import get_settings
from .db import conn, db_available, migrate
from .explain import explain
from .models import AgentMessage, InteractionEvent, Job, Profile, RawJob, ScoreRequest, SearchRequest
from .rerank import rerank_scores
from .retrieval import PostgresBackend, search
from .scoring import current_weights, score_job
from .telemetry import LatencyMiddleware, start_writer

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("match_engine")
EVAL_RESULTS = Path(__file__).resolve().parent.parent / "eval" / "results.json"
_scheduler = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _scheduler
    s = get_settings()
    if db_available():
        try:
            migrate()
            start_writer()
        except Exception as e:
            log.error("database migration failed: %s", e)
    if s.ingest_enabled and db_available():
        from apscheduler.schedulers.background import BackgroundScheduler

        from .ingest.pipeline import run_ingestion

        _scheduler = BackgroundScheduler(timezone="UTC")
        _scheduler.add_job(run_ingestion, "interval", hours=s.ingest_interval_hours, id="ingest",
                           max_instances=1, coalesce=True,
                           next_run_time=datetime.now(timezone.utc) + timedelta(minutes=1))
        _scheduler.start()
        log.info("scheduled ingestion every %.1fh", s.ingest_interval_hours)
    yield
    if _scheduler:
        _scheduler.shutdown(wait=False)


app = FastAPI(title="NitiAI Match Engine", version="1.0.0", lifespan=lifespan)
app.add_middleware(LatencyMiddleware)


def internal(x_internal_token: str | None = Header(default=None)):
    expected = get_settings().match_engine_token
    if expected and x_internal_token != expected:
        raise HTTPException(401, "invalid internal token")


def require_db():
    if not db_available():
        raise HTTPException(503, "DATABASE_URL not configured")


def _profile(user_id: str | None, profile: Profile | None) -> Profile:
    if profile:
        return profile
    if not user_id:
        raise HTTPException(422, "user_id or profile is required")
    require_db()
    from .profiles import load_profile

    try:
        return load_profile(user_id)
    except LookupError as e:
        raise HTTPException(404, str(e))


@app.get("/health")
def health():
    s = get_settings()
    db = False
    if db_available():
        try:
            with conn() as c:
                c.execute("SELECT 1")
            db = True
        except Exception:
            db = False
    return {"status": "ok", "db": db, "embedder": s.embedder, "embedding_model": s.embedding_model,
            "reranker": s.reranker, "llm": llm.available(), "weights": current_weights(),
            "scheduler": bool(_scheduler)}


# ------------------------------------------------------------------ matching

@app.post("/match/search", dependencies=[Depends(internal)])
def match_search(req: SearchRequest):
    require_db()
    prof = _profile(req.user_id, req.profile)
    timings: dict = {}
    t0 = time.perf_counter()
    res = search(PostgresBackend(), prof, query=req.query, k=req.k, mode=req.mode,
                 location=req.location, remote_only=req.remote_only, timings=timings)
    timings["total_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    return {"results": [r.model_dump(mode="json") for r in res], "timings": timings, "mode": req.mode,
            "profile_skills": prof.skills}


@app.post("/match/score", dependencies=[Depends(internal)])
def match_score(req: ScoreRequest):
    """Score caller-supplied jobs (e.g. a live API response) for a user. No retrieval step."""
    prof = _profile(req.user_id, req.profile)
    if not req.jobs:
        return {"scores": []}
    sem = rerank_scores(prof.as_text(), [j.as_text() for j in req.jobs])
    return {"scores": [score_job(prof, j, s).model_dump() for j, s in zip(req.jobs, sem)]}


class ExplainRequest(BaseModel):
    user_id: str | None = None
    profile: Profile | None = None
    job_id: str | None = None
    job: Job | None = None
    use_llm: bool = True


@app.post("/match/explain", dependencies=[Depends(internal)])
def match_explain(req: ExplainRequest):
    prof = _profile(req.user_id, req.profile)
    job = req.job
    if job is None:
        if not req.job_id:
            raise HTTPException(422, "job_id or job is required")
        require_db()
        job = PostgresBackend().get_jobs([req.job_id]).get(req.job_id)
        if not job:
            raise HTTPException(404, "job not found")
    sem = rerank_scores(prof.as_text(), [job.as_text()])[0]
    m = score_job(prof, job, sem)
    return {"match": m.model_dump(), "explanation": explain(prof, job, m, use_llm=req.use_llm).model_dump()}


# ------------------------------------------------------------------ jobs

@app.post("/jobs/upsert", dependencies=[Depends(internal)])
def jobs_upsert(raws: list[RawJob], background: BackgroundTasks):
    require_db()
    from .ingest.pipeline import embed_pending, upsert

    out = upsert(raws)
    background.add_task(embed_pending)
    return out


@app.post("/jobs/ingest", dependencies=[Depends(internal)])
def jobs_ingest(background: BackgroundTasks, queries: list[str] | None = None, location: str | None = None):
    require_db()
    from .ingest.pipeline import run_ingestion

    background.add_task(run_ingestion, queries, location)
    return {"started": True}


@app.get("/jobs/stats", dependencies=[Depends(internal)])
def jobs_stats():
    require_db()
    with conn() as c:
        stats = c.execute("""SELECT count(*) FILTER (WHERE is_active) AS active, count(*) AS total,
                                    count(*) FILTER (WHERE embedding IS NOT NULL) AS embedded,
                                    count(DISTINCT source) AS sources FROM jobs""").fetchone()
        runs = c.execute("SELECT * FROM ingest_runs ORDER BY id DESC LIMIT 5").fetchall()
    return {"jobs": stats, "recent_runs": runs}


# ------------------------------------------------------------------ events / personalisation

@app.post("/events", dependencies=[Depends(internal)])
def log_events(events: list[InteractionEvent]):
    require_db()
    with conn() as c:
        with c.cursor() as cur:
            cur.executemany(
                "INSERT INTO job_interactions (user_id, job_id, event, position, score, features) VALUES (%s,%s,%s,%s,%s,%s)",
                [(e.user_id, e.job_id, e.event, e.position, e.score, Jsonb(e.features) if e.features else None) for e in events])
    return {"logged": len(events)}


@app.post("/ltr/train", dependencies=[Depends(internal)])
def ltr_train():
    require_db()
    from .ltr import train_and_save

    return train_and_save()


# ------------------------------------------------------------------ agent

@app.post("/agent/chat", dependencies=[Depends(internal)])
def agent_chat(msg: AgentMessage):
    require_db()
    if not llm.available():
        raise HTTPException(503, "GEMINI_API_KEY not configured")
    from .agent import Agent

    try:
        return Agent(msg.user_id).chat(msg.message)
    except llm.LLMUnavailable as e:
        raise HTTPException(502, str(e))


@app.get("/agent/memory/{user_id}", dependencies=[Depends(internal)])
def agent_memory(user_id: str):
    require_db()
    from . import memory

    return {"memory": memory.recall(user_id), "messages": memory.recent_messages(user_id, 20)}


class ForgetRequest(BaseModel):
    kind: str
    key: str


@app.delete("/agent/memory/{user_id}", dependencies=[Depends(internal)])
def agent_forget(user_id: str, req: ForgetRequest):
    require_db()
    from . import memory

    return {"deleted": memory.forget(user_id, req.kind, req.key)}


# ------------------------------------------------------------------ dashboards

@app.get("/market/trends", dependencies=[Depends(internal)])
def market_trends(days: int = 30):
    require_db()
    from .market import trends

    return trends(days)


@app.get("/metrics/summary", dependencies=[Depends(internal)])
def metrics_summary(hours: int = 24):
    require_db()
    from .telemetry import summary

    out = summary(hours)
    out["eval"] = json.loads(EVAL_RESULTS.read_text()) if EVAL_RESULTS.exists() else None
    return out
