"""Tool-using career agent (Gemini function calling) with persistent memory."""
from __future__ import annotations

import json
import logging
from collections import Counter

from . import llm, memory
from .db import conn
from .explain import explain
from .models import Profile
from .profiles import load_profile
from .retrieval import PostgresBackend, search
from .scoring import score_job

log = logging.getLogger("agent")
MAX_STEPS = 5

TOOLS = [
    {"name": "search_jobs", "description": "Search stored, live job postings ranked for this user. Returns score, matched and missing skills.",
     "parameters": {"type": "OBJECT", "properties": {
         "query": {"type": "STRING", "description": "Role or keywords. Omit to use the user's profile."},
         "location": {"type": "STRING"}, "remote_only": {"type": "BOOLEAN"},
         "k": {"type": "INTEGER", "description": "How many results, 1-10"}}}},
    {"name": "get_profile", "description": "The user's skills, experience, location, preferences and remembered facts.",
     "parameters": {"type": "OBJECT", "properties": {}}},
    {"name": "skill_gap", "description": "Skills the user lacks for a job (job_id) or for a target role across current postings.",
     "parameters": {"type": "OBJECT", "properties": {"job_id": {"type": "STRING"}, "target_role": {"type": "STRING"}}}},
    {"name": "explain_match", "description": "Grounded explanation of how well the user fits one job.",
     "parameters": {"type": "OBJECT", "properties": {"job_id": {"type": "STRING"}}, "required": ["job_id"]}},
    {"name": "save_job", "description": "Save a job to the user's application tracker. Only when the user asks to save/track it.",
     "parameters": {"type": "OBJECT", "properties": {"job_id": {"type": "STRING"}, "notes": {"type": "STRING"}},
                    "required": ["job_id"]}},
    {"name": "remember", "description": ("Store a durable fact the user stated about themselves. kind=preference uses keys "
                                         "work_mode (remote|onsite|hybrid), location (';'-separated), roles (';'-separated), "
                                         "skills_to_add; kind=goal|constraint|note use any short key."),
     "parameters": {"type": "OBJECT", "properties": {"kind": {"type": "STRING"}, "key": {"type": "STRING"},
                                                     "value": {"type": "STRING"}}, "required": ["kind", "key", "value"]}},
]

SYSTEM = """You are NitiAI's career coach for students and early-career engineers.
Use tools for anything about jobs, the user's profile or skill gaps; never invent postings, companies or numbers.
When the user states a lasting preference or goal (location, remote, target role, salary floor, deadline), call `remember`.
Cite job titles/companies and scores exactly as returned by tools. Keep answers short and actionable.
Known facts about this user:
{facts}"""


class Agent:
    def __init__(self, user_id: str):
        self.user_id = user_id
        self.backend = PostgresBackend()
        self._profile: Profile | None = None
        self.trace: list[dict] = []

    @property
    def profile(self) -> Profile:
        if self._profile is None:
            self._profile = load_profile(self.user_id)
        return self._profile

    # ---------------- tools
    def search_jobs(self, query: str | None = None, location: str | None = None, remote_only: bool = False, k: int = 5):
        res = search(self.backend, self.profile, query=query, k=max(1, min(int(k or 5), 10)),
                     location=location, remote_only=bool(remote_only))
        return {"results": [{"job_id": r.job.id, "title": r.job.title, "company": r.job.company, "location": r.job.location,
                             "remote": r.job.remote, "score": r.match.score, "matched": r.match.matched_skills[:8],
                             "missing": r.match.missing_skills[:8], "url": r.job.url} for r in res]}

    def get_profile(self):
        p = self.profile
        return {"title": p.title, "skills": p.skills, "years_experience": p.years_experience, "location": p.location,
                "work_mode": p.work_mode, "preferred_roles": p.preferred_roles,
                "preferred_locations": p.preferred_locations,
                "memory": [{k: m[k] for k in ("kind", "key", "value")} for m in memory.recall(self.user_id)]}

    def _job(self, job_id: str):
        jobs = self.backend.get_jobs([job_id])
        if job_id not in jobs:
            raise LookupError(f"job {job_id} not found")
        return jobs[job_id]

    def skill_gap(self, job_id: str | None = None, target_role: str | None = None):
        have = {s.lower() for s in self.profile.skills}
        if job_id:
            job = self._job(job_id)
            m = score_job(self.profile, job, 0.5)
            return {"job": job.title, "matched": m.matched_skills, "missing": m.missing_skills}
        role = target_role or self.profile.title or "software engineer"
        ids = [d for d, _ in self.backend.bm25(role, 60, None, False)]
        jobs = self.backend.get_jobs(ids)
        cnt = Counter(s for j in jobs.values() for s in j.skills)
        n = max(1, len(jobs))
        missing = [{"skill": s, "share_of_postings": round(c / n, 2)} for s, c in cnt.most_common(40) if s.lower() not in have][:10]
        present = [{"skill": s, "share_of_postings": round(c / n, 2)} for s, c in cnt.most_common(40) if s.lower() in have][:10]
        return {"target_role": role, "postings_analysed": len(jobs), "missing": missing, "already_have": present}

    def explain_match(self, job_id: str):
        job = self._job(job_id)
        from .rerank import rerank_scores

        sem = rerank_scores(self.profile.as_text(), [job.as_text()])[0]
        m = score_job(self.profile, job, sem)
        return explain(self.profile, job, m).model_dump()

    def save_job(self, job_id: str, notes: str | None = None):
        job = self._job(job_id)
        with conn() as c:
            c.execute("""INSERT INTO job_applications (user_id, job_id, job_title, company, location, job_url, status, notes)
                         VALUES (%s,%s,%s,%s,%s,%s,'saved',%s)
                         ON CONFLICT (user_id, job_id) DO UPDATE SET notes = COALESCE(EXCLUDED.notes, job_applications.notes),
                                                                    updated_at = now()""",
                      [self.user_id, job.id, job.title, job.company or "Unknown Company", job.location or "Remote", job.url, notes])
            c.execute("INSERT INTO job_interactions (user_id, job_id, event) VALUES (%s,%s,'save')", [self.user_id, job.id])
        return {"saved": True, "title": job.title, "company": job.company}

    def remember(self, kind: str, key: str, value: str):
        out = memory.remember(self.user_id, kind, key, value)
        self._profile = None  # preferences feed into matching
        return out

    def call(self, name: str, args: dict):
        fn = {t["name"]: getattr(self, t["name"]) for t in TOOLS}.get(name)
        if not fn:
            return {"error": f"unknown tool {name}"}
        try:
            return fn(**(args or {}))
        except Exception as e:
            log.warning("tool %s failed: %s", name, e)
            return {"error": str(e)[:300]}

    # ---------------- loop
    def chat(self, message: str) -> dict:
        facts = "\n".join(f"- {m['kind']}/{m['key']}: {m['value']}" for m in memory.recall(self.user_id)) or "- none yet"
        history = memory.recent_messages(self.user_id)
        contents = [{"role": "model" if h["role"] == "assistant" else "user", "parts": [{"text": h["content"]}]} for h in history]
        contents.append({"role": "user", "parts": [{"text": message}]})
        reply = ""
        for _ in range(MAX_STEPS):
            out = llm.chat_with_tools(SYSTEM.format(facts=facts), contents, TOOLS)
            parts = out.get("parts", [])
            calls = [p["functionCall"] for p in parts if "functionCall" in p]
            if not calls:
                reply = "".join(p.get("text", "") for p in parts).strip()
                break
            contents.append({"role": "model", "parts": parts})
            responses = []
            for fc in calls:
                result = self.call(fc["name"], fc.get("args", {}))
                self.trace.append({"tool": fc["name"], "args": fc.get("args", {}),
                                   "ok": "error" not in result if isinstance(result, dict) else True})
                responses.append({"functionResponse": {"name": fc["name"], "response": {"content": json.loads(json.dumps(result, default=str))}}})
            contents.append({"role": "user", "parts": responses})
        if not reply:
            reply = "I couldn't finish that request. Please try rephrasing it."
        memory.add_message(self.user_id, "user", message)
        memory.add_message(self.user_id, "assistant", reply)
        return {"response": reply, "tool_calls": self.trace}
