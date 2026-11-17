"""Market trends computed from the stored jobs table (RapidAPI + Adzuna postings)."""
from __future__ import annotations

from .db import conn

ROLE_BUCKETS = {
    "AI/ML Engineer": ["machine learning", "ml engineer", "ai engineer", "deep learning", "nlp", "computer vision", "llm"],
    "Data Scientist": ["data scientist"],
    "Data Engineer": ["data engineer", "etl", "big data"],
    "Data Analyst": ["data analyst", "business analyst", "bi analyst"],
    "Backend Developer": ["backend", "back end", "back-end", "java developer", "python developer", "node"],
    "Frontend Developer": ["frontend", "front end", "front-end", "react developer", "ui developer"],
    "Full Stack Developer": ["full stack", "fullstack", "full-stack", "mern"],
    "DevOps / Cloud Engineer": ["devops", "sre", "site reliability", "cloud engineer", "platform engineer"],
    "Mobile Developer": ["android", "ios", "flutter", "react native", "mobile"],
    "Security Engineer": ["security", "cyber", "soc analyst", "penetration"],
    "QA / Test Engineer": ["qa", "test engineer", "sdet", "automation test"],
}


def trends(days: int = 30) -> dict:
    with conn() as c:
        total = c.execute("SELECT count(*) AS n FROM jobs WHERE is_active AND first_seen_at > now() - make_interval(days => %s)",
                          [days]).fetchone()["n"]
        skills = c.execute(
            """SELECT s AS skill, count(*) AS n FROM jobs, unnest(skills) s
               WHERE is_active AND first_seen_at > now() - make_interval(days => %s)
               GROUP BY s ORDER BY n DESC LIMIT 25""", [days]).fetchall()
        rising = c.execute(
            """WITH w AS (
                 SELECT s, count(*) FILTER (WHERE first_seen_at > now() - interval '7 days') AS recent,
                           count(*) FILTER (WHERE first_seen_at <= now() - interval '7 days'
                                            AND first_seen_at > now() - interval '28 days') AS prior
                 FROM jobs, unnest(skills) s GROUP BY s)
               SELECT s AS skill, recent, prior,
                      round(((recent::numeric / 1) - (prior::numeric / 3)) / greatest(prior::numeric / 3, 1) * 100, 0) AS growth_pct
               FROM w WHERE recent >= 3 ORDER BY growth_pct DESC LIMIT 10""").fetchall()
        titles = c.execute("SELECT lower(title) AS t, salary_min, salary_max, remote FROM jobs WHERE is_active "
                           "AND first_seen_at > now() - make_interval(days => %s)", [days]).fetchall()
        locs = c.execute("""SELECT split_part(location, ',', 1) AS city, count(*) AS n FROM jobs
                            WHERE is_active AND location IS NOT NULL GROUP BY 1 ORDER BY n DESC LIMIT 10""").fetchall()
    roles = []
    for role, kws in ROLE_BUCKETS.items():
        rows = [r for r in titles if any(k in r["t"] for k in kws)]
        sal = [float(r["salary_min"] + r["salary_max"]) / 2 for r in rows if r["salary_min"] and r["salary_max"]]
        sal.sort()
        roles.append({"role": role, "postings": len(rows), "share": round(len(rows) / max(1, total), 3),
                      "remote_share": round(sum(r["remote"] for r in rows) / max(1, len(rows)), 2),
                      "median_salary": sal[len(sal) // 2] if sal else None, "salary_samples": len(sal)})
    roles.sort(key=lambda r: -r["postings"])
    return {"window_days": days, "total_postings": total, "roles": roles,
            "top_skills": [{"skill": r["skill"], "postings": r["n"], "share": round(r["n"] / max(1, total), 3)} for r in skills],
            "rising_skills": [{**r, "growth_pct": float(r["growth_pct"])} for r in rising],
            "top_locations": locs, "source": "jobs table (RapidAPI LinkedIn + Adzuna)"}
