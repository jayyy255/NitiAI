"""Ingestion: normalise -> deduplicate -> upsert -> expire stale -> embed what changed."""
from __future__ import annotations

import hashlib
import json
import logging
import re
import unicodedata
from datetime import datetime, timedelta, timezone

from psycopg.types.json import Jsonb

from ..config import get_settings
from ..db import conn
from ..embeddings import embed
from ..models import Job, RawJob
from ..skills import extract_skills, parse_min_years
from . import sources

log = logging.getLogger("ingest")

_SUFFIX = re.compile(r"\b(pvt|private|ltd|limited|inc|llc|llp|corp|corporation|technologies|technology|solutions|india)\b\.?", re.I)
_REMOTE = re.compile(r"\b(remote|work from home|wfh)\b", re.I)
_HTML = re.compile(r"<[^>]+>")


def _norm(s: str | None) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def dedup_key(title: str, company: str | None, location: str | None) -> str:
    """Same role at the same company in the same city = same job, whichever board it came from."""
    t = _norm(title)
    c = _norm(_SUFFIX.sub("", company or ""))
    city = _norm((location or "").split(",")[0])
    return hashlib.sha1(f"{t}|{c}|{city}".encode()).hexdigest()


def clean_description(text: str) -> str:
    return re.sub(r"\s+", " ", _HTML.sub(" ", text or "")).strip()


def content_hash(title: str, company: str | None, description: str) -> str:
    return hashlib.sha1(f"{title}\n{company}\n{description}".encode()).hexdigest()


def normalise(raw: RawJob) -> dict:
    desc = clean_description(raw.description)
    text = f"{raw.title}\n{desc}"
    remote = raw.remote if raw.remote is not None else bool(_REMOTE.search(f"{raw.title} {raw.location or ''} {desc[:500]}"))
    return {
        "source": raw.source, "external_id": raw.external_id, "title": raw.title.strip(),
        "company": (raw.company or "").strip() or None, "location": (raw.location or "").strip() or None,
        "remote": remote, "description": desc, "url": raw.url, "salary_min": raw.salary_min,
        "salary_max": raw.salary_max, "salary_text": raw.salary_text, "skills": extract_skills(text),
        "min_years": parse_min_years(text), "posted_at": raw.posted_at,
        "dedup_key": dedup_key(raw.title, raw.company, raw.location),
        "content_hash": content_hash(raw.title, raw.company, desc),
    }


def dedupe_batch(rows: list[dict]) -> list[dict]:
    """Within one batch keep the richest record per dedup_key (longest description)."""
    best: dict[str, dict] = {}
    for r in rows:
        cur = best.get(r["dedup_key"])
        if cur is None or len(r["description"]) > len(cur["description"]):
            best[r["dedup_key"]] = r
    return list(best.values())


_UPSERT = """
INSERT INTO jobs (source, external_id, dedup_key, title, company, location, remote, description, url,
                  salary_min, salary_max, salary_text, skills, min_years, posted_at, content_hash)
VALUES (%(source)s, %(external_id)s, %(dedup_key)s, %(title)s, %(company)s, %(location)s, %(remote)s, %(description)s,
        %(url)s, %(salary_min)s, %(salary_max)s, %(salary_text)s, %(skills)s, %(min_years)s, %(posted_at)s, %(content_hash)s)
ON CONFLICT (dedup_key) DO UPDATE SET
    last_seen_at = now(),
    is_active = true,
    -- keep the longer description if two boards carry the same job
    description = CASE WHEN length(EXCLUDED.description) > length(jobs.description) THEN EXCLUDED.description ELSE jobs.description END,
    content_hash = CASE WHEN length(EXCLUDED.description) > length(jobs.description) THEN EXCLUDED.content_hash ELSE jobs.content_hash END,
    skills = CASE WHEN length(EXCLUDED.description) > length(jobs.description) THEN EXCLUDED.skills ELSE jobs.skills END,
    min_years = COALESCE(jobs.min_years, EXCLUDED.min_years),
    url = COALESCE(jobs.url, EXCLUDED.url),
    salary_min = COALESCE(jobs.salary_min, EXCLUDED.salary_min),
    salary_max = COALESCE(jobs.salary_max, EXCLUDED.salary_max),
    salary_text = COALESCE(jobs.salary_text, EXCLUDED.salary_text),
    posted_at = LEAST(jobs.posted_at, EXCLUDED.posted_at)
RETURNING id::text AS id, (xmax = 0) AS inserted
"""


def upsert(raws: list[RawJob]) -> dict:
    rows = dedupe_batch([normalise(r) for r in raws if r.title])
    inserted = updated = 0
    ids: list[str] = []
    with conn() as c:
        for r in rows:
            res = c.execute(_UPSERT, r).fetchone()
            ids.append(res["id"])
            inserted += int(res["inserted"])
            updated += int(not res["inserted"])
    return {"received": len(raws), "unique": len(rows), "inserted": inserted, "updated": updated, "ids": ids}


def expire_stale() -> int:
    s = get_settings()
    now = datetime.now(timezone.utc)
    with conn() as c:
        cur = c.execute(
            """UPDATE jobs SET is_active = false
               WHERE is_active AND (last_seen_at < %s OR (posted_at IS NOT NULL AND posted_at < %s))""",
            [now - timedelta(days=s.stale_after_days), now - timedelta(days=s.max_posting_age_days)])
        return cur.rowcount or 0


def embed_pending(batch: int = 64) -> int:
    """Embed only jobs that are new or whose content changed since the last embedding."""
    done = 0
    while True:
        with conn() as c:
            rows = c.execute(
                """SELECT id::text AS id, title, company, location, description, skills, content_hash
                   FROM jobs WHERE is_active AND (embedded_hash IS DISTINCT FROM content_hash) LIMIT %s""",
                [batch]).fetchall()
            if not rows:
                return done
            texts = [Job(id=r["id"], title=r["title"], company=r["company"], location=r["location"],
                         description=r["description"] or "", skills=r["skills"] or []).as_text() for r in rows]
            vecs = embed(texts)
            for r, v in zip(rows, vecs):
                c.execute("UPDATE jobs SET embedding = %s, embedded_hash = %s WHERE id = %s",
                          [v, r["content_hash"], r["id"]])
            done += len(rows)


def run_ingestion(queries: list[str] | None = None, location: str | None = None) -> dict:
    s = get_settings()
    queries = queries or [q.strip() for q in s.ingest_queries.split(",") if q.strip()]
    location = location or s.ingest_location
    with conn() as c:
        run_id = c.execute("INSERT INTO ingest_runs DEFAULT VALUES RETURNING id").fetchone()["id"]
    raws: list[RawJob] = []
    errors: list[dict] = []
    for q in queries:
        for name, fn in (("rapidapi", lambda q=q: sources.fetch_rapidapi(q, location)),
                         ("adzuna", lambda q=q: sources.fetch_adzuna(q))):
            try:
                raws += fn()
            except Exception as e:  # one failing source must not stop the run
                errors.append({"source": name, "query": q, "error": str(e)[:300]})
                log.warning("ingest %s/%s failed: %s", name, q, e)
    stats = upsert(raws) if raws else {"inserted": 0, "updated": 0, "unique": 0}
    expired = expire_stale()
    embedded = embed_pending()
    with conn() as c:
        c.execute("""UPDATE ingest_runs SET finished_at = now(), fetched = %s, inserted = %s, updated = %s,
                     expired = %s, embedded = %s, errors = %s WHERE id = %s""",
                  [len(raws), stats["inserted"], stats["updated"], expired, embedded, Jsonb(errors), run_id])
    out = {"run_id": run_id, "fetched": len(raws), "unique": stats.get("unique", 0), "inserted": stats["inserted"],
           "updated": stats["updated"], "expired": expired, "embedded": embedded, "errors": errors}
    log.info("ingestion finished: %s", json.dumps({k: v for k, v in out.items() if k != "errors"}))
    return out
