"""End-to-end checks against a real Postgres + pgvector. Skipped unless TEST_DATABASE_URL is set:

  docker run -d --name niti-pg -e POSTGRES_PASSWORD=test -p 55432:5432 pgvector/pgvector:pg16
  psql ... -f server/schema/schema.sql
  TEST_DATABASE_URL=postgresql://postgres:test@localhost:55432/postgres pytest tests/test_integration_pg.py
"""
import json
import os
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL not set")

DATA = Path(__file__).resolve().parent.parent / "eval" / "data"


@pytest.fixture(scope="module")
def seeded():
    from app.db import conn, migrate
    from app.ingest.pipeline import embed_pending, upsert
    from app.models import RawJob

    migrate()
    with conn() as c:
        c.execute("TRUNCATE jobs, job_interactions, coach_memory, coach_messages, request_logs, ingest_runs")
    raws = [RawJob(source="eval", external_id=j["id"], title=j["title"], company=j["company"], location=j["location"],
                   description=j["description"], remote=j["remote"])
            for j in map(json.loads, (DATA / "jobs.jsonl").read_text().splitlines())]
    first = upsert(raws)
    embedded = embed_pending()
    uid = str(uuid.uuid4())
    with conn() as c:
        c.execute("""INSERT INTO users (id, username, email, password, location, preferred_work_mode, career_goal_short)
                     VALUES (%s,'t',%s,'x','Bengaluru, India','hybrid','Backend Engineer')""", [uid, f"{uid}@t.dev"])
        c.execute("""INSERT INTO resume_info (user_id, professional_title, years_of_experience, technical_skills, experience)
                     VALUES (%s,'Backend Developer',1,%s,%s)""",
                  [uid, ["nodejs", "Express", "postgres", "Docker", "REST"],
                   json.dumps([{"title": "Intern", "company": "PayLite", "description": "Built Node.js APIs on PostgreSQL"}])])
    return {"first": first, "embedded": embedded, "uid": uid, "n": len(raws)}


def test_ingest_dedup_and_incremental_embedding(seeded):
    from app.ingest.pipeline import embed_pending, upsert
    from app.models import RawJob

    assert seeded["first"]["inserted"] == seeded["n"] and seeded["embedded"] == seeded["n"]
    again = upsert([RawJob(source="adzuna", title="Backend Engineer (Node.js)", company="Razorpay Pvt Ltd",
                           location="Bengaluru", description="short")])
    assert again["inserted"] == 0 and again["updated"] == 1
    assert embed_pending() == 0  # shorter duplicate does not replace content -> nothing to re-embed


def test_expire_stale(seeded):
    from app.db import conn
    from app.ingest.pipeline import expire_stale

    with conn() as c:
        c.execute("UPDATE jobs SET last_seen_at = now() - interval '60 days' WHERE external_id = 'j50'")
    assert expire_stale() == 1
    with conn() as c:
        assert c.execute("SELECT is_active FROM jobs WHERE external_id='j50'").fetchone()["is_active"] is False
        c.execute("UPDATE jobs SET last_seen_at = now(), is_active = true WHERE external_id = 'j50'")


def test_profile_and_hybrid_search(seeded):
    from app.profiles import load_profile
    from app.retrieval import PostgresBackend, search

    p = load_profile(seeded["uid"])
    assert {"Node.js", "PostgreSQL", "REST APIs"} <= set(p.skills) and p.years_experience == 1
    for mode in ("bm25", "vector", "hybrid", "hybrid_rerank"):
        res = search(PostgresBackend(), p, k=10, mode=mode)
        assert len(res) == 10, mode
        assert "Backend" in res[0].job.title or "Full Stack" in res[0].job.title, (mode, res[0].job.title)
    res = search(PostgresBackend(), p, k=5, remote_only=True)
    assert res and all(r.job.remote for r in res)


def test_events_metrics_market(seeded):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        H = {"X-Internal-Token": "test-token"}
        r = client.post("/match/search", json={"user_id": seeded["uid"], "k": 5}, headers=H)
        assert r.status_code == 200 and len(r.json()["results"]) == 5
        top = r.json()["results"][0]
        ev = [{"user_id": seeded["uid"], "job_id": top["job"]["id"], "event": "click", "position": 1,
               "score": top["match"]["score"], "features": top["match"]["components"]}]
        assert client.post("/events", json=ev, headers=H).json() == {"logged": 1}
        ex = client.post("/match/explain", json={"user_id": seeded["uid"], "job_id": top["job"]["id"]}, headers=H)
        assert ex.status_code == 200 and ex.json()["explanation"]["strengths"]
        assert client.post("/ltr/train", headers=H).json()["trained"] is False
        m = client.get("/market/trends", headers=H).json()
        assert m["total_postings"] >= 70 and m["top_skills"]
        import time
        time.sleep(1.5)  # request-log writer is async
        s = client.get("/metrics/summary", headers=H).json()
        assert s["jobs"]["active"] >= 70 and any(x["route"] == "/match/search" for x in s["routes"])


def test_agent_tools_without_llm(seeded):
    from app.agent import Agent
    from app.db import conn

    a = Agent(seeded["uid"])
    found = a.call("search_jobs", {"query": "data engineer", "k": 3})["results"]
    assert len(found) == 3 and any("Data" in f["title"] for f in found)
    gap = a.call("skill_gap", {"target_role": "data engineer"})
    assert gap["postings_analysed"] > 0 and gap["missing"]
    assert a.call("save_job", {"job_id": found[0]["job_id"]})["saved"]
    a.call("remember", {"kind": "preference", "key": "work_mode", "value": "remote"})
    assert a.profile.work_mode == "remote"
    with conn() as c:
        assert c.execute("SELECT status FROM job_applications WHERE user_id=%s", [seeded["uid"]]).fetchone()["status"] == "saved"
    assert "error" in a.call("save_job", {"job_id": str(uuid.uuid4())})
