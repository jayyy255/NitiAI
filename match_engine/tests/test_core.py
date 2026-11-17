import numpy as np
import pytest

from app.explain import template_explanation, verify_evidence
from app.ingest.pipeline import dedup_key, dedupe_batch, normalise
from app.ltr import auc, train_from_rows
from app.models import Evidence, Job, Profile, RawJob
from app.retrieval import MemoryBackend, rrf, search
from app.scoring import DEFAULT_WEIGHTS, score_job
from app.skills import canonicalize, extract_skills, parse_min_years


def test_extract_skills_handles_symbols_and_ambiguity():
    s = extract_skills("We use C++, C#, Node.js, React Native and CI/CD. You should go to the office.")
    assert {"C++", "C#", "Node.js", "React Native", "CI/CD"} <= set(s)
    assert "Go" not in s and "React" not in s


def test_canonicalize_aliases():
    assert canonicalize("reactjs") == "React"
    assert canonicalize("Postgres") == "PostgreSQL"
    assert canonicalize("k8s") == "Kubernetes"
    assert canonicalize("Some Unknown Tool") == "Some Unknown Tool"


@pytest.mark.parametrize("text,expected", [
    ("Requires 3+ years of experience in Python", 3.0),
    ("2-4 years of relevant experience", 2.0),
    ("Freshers welcome", 0.0),
    ("Great culture", None),
])
def test_parse_min_years(text, expected):
    assert parse_min_years(text) == expected


def _job(**kw):
    base = dict(id="j", title="Backend Engineer", company="Acme", location="Bengaluru, India",
                description="Node.js, PostgreSQL and Docker. Requires 2+ years of experience.")
    base.update(kw)
    return Job(**base)


def test_score_is_deterministic_and_explainable():
    p = Profile(skills=["Node.js", "PostgreSQL"], years_experience=3, location="Bengaluru", preferred_roles=["Backend Engineer"])
    m1 = score_job(p, _job(), semantic=0.6)
    m2 = score_job(p, _job(), semantic=0.6)
    assert m1 == m2
    assert m1.matched_skills == ["Node.js", "PostgreSQL"] and m1.missing_skills == ["Docker"]
    expected = sum(DEFAULT_WEIGHTS[k] * getattr(m1.components, k) for k in DEFAULT_WEIGHTS)
    assert m1.score == round(100 * expected)


def test_score_monotonic_in_skills_and_experience():
    j = _job()
    weak = score_job(Profile(skills=[], years_experience=0), j, 0.5).score
    strong = score_job(Profile(skills=["Node.js", "PostgreSQL", "Docker"], years_experience=2), j, 0.5).score
    assert strong > weak


def test_remote_preference():
    p = Profile(work_mode="remote")
    assert score_job(p, _job(location="Remote", remote=True), 0.5).components.location == 1.0
    assert score_job(p, _job(), 0.5).components.location < 0.5


def test_rrf_fuses_rankings():
    fused = rrf([[("a", 1), ("b", 1)], [("b", 1), ("c", 1)]], k=60)
    assert fused[0][0] == "b"


def test_memory_backend_search_modes():
    jobs = [_job(id="1"), _job(id="2", title="Data Analyst", description="SQL, Excel and Tableau dashboards"),
            _job(id="3", title="Frontend Engineer", description="React and TypeScript")]
    b = MemoryBackend(jobs)
    p = Profile(title="Backend Engineer", skills=["Node.js", "PostgreSQL"])
    for mode in ("bm25", "vector", "hybrid", "hybrid_rerank"):
        res = search(b, p, k=3, mode=mode)
        assert res and res[0].job.id == "1", mode
        assert 0 <= res[0].match.score <= 100


def test_dedup_key_ignores_company_suffix_and_case():
    assert dedup_key("Backend Engineer", "Acme Technologies Pvt Ltd", "Bengaluru, Karnataka") == \
        dedup_key("backend engineer", "ACME", "Bengaluru")
    assert dedup_key("Backend Engineer", "Acme", "Pune") != dedup_key("Backend Engineer", "Acme", "Bengaluru")


def test_dedupe_batch_keeps_richest():
    a = normalise(RawJob(source="rapidapi", title="ML Engineer", company="X", location="Pune", description="short"))
    b = normalise(RawJob(source="adzuna", title="ML Engineer", company="X Ltd", location="Pune",
                         description="much longer description with Python and PyTorch"))
    out = dedupe_batch([a, b])
    assert len(out) == 1 and out[0]["source"] == "adzuna"
    assert "PyTorch" in out[0]["skills"]


def test_normalise_strips_html_and_detects_remote():
    r = normalise(RawJob(source="x", title="Engineer", description="<p>Work from home</p><b>Python</b>"))
    assert "<" not in r["description"] and r["remote"] and r["skills"] == ["Python"]


def test_evidence_verifier_rejects_fabrication():
    ptext, jtext = "Skills: Python, Docker", "We need Python and Kubernetes"
    assert verify_evidence(Evidence(skill="Python", profile_quote="Python, Docker", job_quote="need Python"), ptext, jtext)
    assert not verify_evidence(Evidence(skill="Kubernetes", profile_quote="Kubernetes expert", job_quote="Kubernetes"), ptext, jtext)
    assert not verify_evidence(Evidence(skill="Python", profile_quote="Docker", job_quote="need Python"), ptext, jtext)


def test_template_explanation_is_grounded():
    p = Profile(skills=["Node.js", "PostgreSQL"], experience_text="Built APIs in Node.js on PostgreSQL")
    j = _job()
    m = score_job(p, j, 0.5)
    e = template_explanation(p, j, m)
    assert e.strengths and all(verify_evidence(s, p.as_text(), j.as_text()) for s in e.strengths)
    assert e.gaps == ["Docker"]


def test_auc():
    assert auc(np.array([0.9, 0.8, 0.1]), np.array([1, 1, 0])) == 1.0


def test_ltr_learns_signal():
    rng = np.random.default_rng(1)
    rows = []
    for _ in range(1500):
        f = {k: float(rng.random()) for k in DEFAULT_WEIGHTS}
        clicked = rng.random() < f["skill_overlap"] ** 2
        rows.append({"event": "click" if clicked else "impression", "features": f})
    res = train_from_rows(rows)
    assert res["trained"] and res["weights"]["skill_overlap"] > DEFAULT_WEIGHTS["skill_overlap"]
    assert abs(sum(res["weights"].values()) - 1) < 1e-6


def test_ltr_needs_data():
    assert not train_from_rows([])["trained"]
