"""Build a matching Profile from the NitiAI Postgres tables (users, resume_info, skills,
preferences, experience) plus anything the career coach has remembered."""
from __future__ import annotations

from datetime import date

from .db import conn
from .models import Profile
from .skills import canonical_set


def _years_from_rows(rows: list[dict]) -> float | None:
    total = 0.0
    for r in rows:
        s = r.get("start_date")
        if not s:
            continue
        try:
            s = s if isinstance(s, date) else date.fromisoformat(str(s)[:10])
            e = r.get("end_date") or date.today()
            e = e if isinstance(e, date) else date.fromisoformat(str(e)[:10])
            total += max(0.0, (e - s).days / 365.25)
        except ValueError:
            continue
    return round(total, 1) if total else None


def load_profile(user_id: str) -> Profile:
    with conn() as c:
        u = c.execute("""SELECT id::text AS id, location, preferred_work_mode, career_goal_short, professional_title,
                                years_of_experience, professional_summary
                         FROM users WHERE id = %s""", [user_id]).fetchone()
        if not u:
            raise LookupError(f"user {user_id} not found")
        ri = c.execute("""SELECT professional_title, years_of_experience, professional_summary, technical_skills,
                                 soft_skills, experience, projects
                          FROM resume_info WHERE user_id = %s""", [user_id]).fetchone() or {}
        sk = [r["skill_name"] for r in c.execute("SELECT skill_name FROM skills WHERE user_id = %s", [user_id]).fetchall()]
        pref = c.execute("SELECT preferred_roles FROM preferences WHERE user_id = %s", [user_id]).fetchone() or {}
        exp_rows = c.execute("SELECT title, organization, description, start_date, end_date FROM experience WHERE user_id = %s",
                             [user_id]).fetchall()
        try:
            mem = c.execute("SELECT kind, key, value FROM coach_memory WHERE user_id = %s", [user_id]).fetchall()
        except Exception:
            mem = []

    skills = canonical_set([*(ri.get("technical_skills") or []), *sk])
    exp_json = ri.get("experience") or []
    proj_json = ri.get("projects") or []
    exp_text = "\n".join(
        [f"{e.get('title', '')} at {e.get('company') or e.get('organization', '')}: {e.get('description', '')}" for e in exp_json if isinstance(e, dict)]
        + [f"{e['title']} at {e['organization']}: {e['description'] or ''}" for e in exp_rows if not exp_json]
        + [f"Project {p.get('name') or p.get('title', '')}: {p.get('description', '')} {' '.join(p.get('technologies') or [])}"
           for p in proj_json if isinstance(p, dict)]
    )
    years = ri.get("years_of_experience") or u.get("years_of_experience")
    if years is None:
        years = _years_from_rows(exp_json if exp_json else exp_rows) or (0.0 if (exp_json or exp_rows or skills) else None)

    roles = list(pref.get("preferred_roles") or [])
    if u.get("career_goal_short"):
        roles.append(u["career_goal_short"])
    prof = Profile(user_id=user_id, title=ri.get("professional_title") or u.get("professional_title") or u.get("career_goal_short"),
                   summary=ri.get("professional_summary") or u.get("professional_summary"), skills=skills,
                   years_experience=float(years) if years is not None else None, location=u.get("location"),
                   work_mode=u.get("preferred_work_mode"), preferred_roles=roles, experience_text=exp_text)
    return apply_memory(prof, mem)


def apply_memory(prof: Profile, mem: list[dict]) -> Profile:
    """Coach-remembered preferences override the static profile."""
    upd: dict = {}
    for m in mem:
        if m["kind"] != "preference":
            continue
        k, v = m["key"], m["value"]
        if k == "work_mode" and v in ("remote", "onsite", "hybrid"):
            upd["work_mode"] = v
        elif k == "location":
            upd["preferred_locations"] = [x.strip() for x in v.split(";") if x.strip()]
        elif k == "roles":
            upd["preferred_roles"] = [x.strip() for x in v.split(";") if x.strip()] + prof.preferred_roles
        elif k == "skills_to_add":
            upd["skills"] = canonical_set(prof.skills + v.split(";"))
    return prof.model_copy(update=upd) if upd else prof
