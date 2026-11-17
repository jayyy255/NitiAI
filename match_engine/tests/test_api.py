import os

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
H = {"X-Internal-Token": "test-token"}
PROFILE = {"title": "Backend Engineer", "skills": ["Node.js", "PostgreSQL"], "years_experience": 1, "location": "Pune"}
JOB = {"id": "x1", "title": "Backend Engineer", "company": "Acme", "location": "Pune, India",
       "description": "Node.js, PostgreSQL, Redis. Requires 1+ years of experience."}


no_db = pytest.mark.skipif(bool(os.environ.get("TEST_DATABASE_URL")), reason="checks DB-less behaviour")


@no_db
def test_health():
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["db"] is False


def test_token_required():
    assert client.post("/match/score", json={"profile": PROFILE, "jobs": [JOB]}).status_code == 401


def test_score_endpoint():
    r = client.post("/match/score", json={"profile": PROFILE, "jobs": [JOB]}, headers=H)
    assert r.status_code == 200
    s = r.json()["scores"][0]
    assert s["job_id"] == "x1" and 0 <= s["score"] <= 100
    assert s["matched_skills"] == ["Node.js", "PostgreSQL"] and s["missing_skills"] == ["Redis"]


def test_explain_endpoint_template_without_llm():
    r = client.post("/match/explain", json={"profile": PROFILE, "job": JOB}, headers=H)
    assert r.status_code == 200
    e = r.json()["explanation"]
    assert e["generator"] == "template" and e["gaps"] == ["Redis"]


@no_db
def test_db_endpoints_report_503_without_database():
    assert client.post("/match/search", json={"profile": PROFILE}, headers=H).status_code == 503
    assert client.post("/events", json=[], headers=H).status_code == 503
