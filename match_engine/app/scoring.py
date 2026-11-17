"""Explainable match score: a weighted sum of five bounded components.

score = 100 * Σ w_i * c_i, with every c_i in [0, 1] and Σ w_i = 1.
Default weights are hand-set; `ltr.py` can learn new ones from click/save/apply logs
and writes them to `ltr_weights.json`, which is picked up here.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .config import get_settings
from .models import Job, MatchScore, Profile, ScoreComponents
from .skills import canonical_set, extract_skills, parse_min_years

DEFAULT_WEIGHTS = {"semantic": 0.35, "skill_overlap": 0.35, "location": 0.10, "preferences": 0.10, "experience": 0.10}
COMPONENTS = list(DEFAULT_WEIGHTS)

_weights_cache: tuple[float, dict] | None = None


def current_weights() -> dict[str, float]:
    """LTR weights if a trained file exists (reloaded when it changes), else defaults."""
    global _weights_cache
    p = Path(get_settings().ltr_weights_path)
    if not p.exists():
        return dict(DEFAULT_WEIGHTS)
    mtime = p.stat().st_mtime
    if _weights_cache and _weights_cache[0] == mtime:
        return dict(_weights_cache[1])
    try:
        data = json.loads(p.read_text())
        w = {k: max(0.0, float(data["weights"].get(k, 0))) for k in COMPONENTS}
        total = sum(w.values()) or 1.0
        w = {k: v / total for k, v in w.items()}
    except Exception:
        w = dict(DEFAULT_WEIGHTS)
    _weights_cache = (mtime, w)
    return dict(w)


_WORD = re.compile(r"[a-z0-9+#]+")
_STOP = {"and", "or", "the", "a", "of", "for", "in", "at", "to", "with", "i", "ii", "iii", "sr", "jr"}
# words shared by most job titles; they say nothing about which role it is
_GENERIC = {"engineer", "developer", "software", "senior", "junior", "lead", "intern", "associate", "specialist",
            "trainee", "graduate", "new", "grad", "staff", "principal", "remote", "sde", "role"}
_REMOTE = re.compile(r"\b(remote|work from home|wfh|anywhere)\b", re.I)


def _tokens(s: str | None) -> set[str]:
    return {t for t in _WORD.findall((s or "").lower()) if t not in _STOP}


def job_skills(job: Job) -> list[str]:
    return canonical_set(job.skills) if job.skills else extract_skills(f"{job.title}\n{job.description}")


def skill_component(profile: Profile, job: Job) -> tuple[float, list[str], list[str]]:
    have = {s.lower(): s for s in canonical_set(profile.skills)}
    need = job_skills(job)
    if not need:
        return 0.5, [], []  # posting lists no recognisable skills: neutral
    matched = [s for s in need if s.lower() in have]
    missing = [s for s in need if s.lower() not in have]
    # Laplace-smoothed coverage so a 1-skill posting can't give a perfect score on its own
    return (len(matched) + 0.5) / (len(need) + 1.0), matched, missing


def _loc_parts(loc: str | None) -> list[str]:
    return [p.strip().lower() for p in re.split(r"[,/|-]", loc or "") if p.strip()]


def location_component(profile: Profile, job: Job) -> tuple[float, str]:
    job_remote = job.remote or bool(_REMOTE.search(f"{job.location or ''} {job.title}"))
    wants_remote = profile.work_mode == "remote"
    if job_remote:
        return 1.0, "remote role"
    if wants_remote:
        return 0.35, "you prefer remote; this role is on-site/hybrid"
    targets = [profile.location, *profile.preferred_locations]
    targets = [t for t in targets if t]
    if not targets:
        return 0.6, "no location preference on profile"
    jparts = _loc_parts(job.location)
    if not jparts:
        return 0.6, "job location not stated"
    best = 0.2
    for t in targets:
        tparts = _loc_parts(t)
        if tparts and jparts and tparts[0] == jparts[0]:
            return 1.0, f"in {job.location}"
        if set(tparts) & set(jparts):
            best = max(best, 0.7)
    return best, ("same region" if best >= 0.7 else f"located in {job.location}")


def preference_component(profile: Profile, job: Job) -> float:
    roles = [r for r in [*profile.preferred_roles, profile.title] if r]
    if not roles:
        return 0.5
    jt = (_tokens(job.title) - _GENERIC) or _tokens(job.title)
    if not jt:
        return 0.5
    best = 0.0
    for r in roles:
        rt = (_tokens(r) - _GENERIC) or _tokens(r)
        if not rt:
            continue
        inter = len(jt & rt)
        best = max(best, inter / min(len(jt), len(rt)) if inter else 0.0)
    return 0.15 + 0.85 * best


def experience_component(profile: Profile, job: Job) -> tuple[float, str]:
    need = job.min_years if job.min_years is not None else parse_min_years(f"{job.title}\n{job.description}")
    have = profile.years_experience
    if need is None:
        return 0.8, "no experience requirement stated"
    if have is None:
        return 0.6, f"asks for {need:g}+ years; your experience is not on your profile"
    gap = have - need
    if gap >= 0:
        if gap > 8:
            return 0.75, f"asks for {need:g}+ years; you have {have:g} (may be over-qualified)"
        return 1.0, f"asks for {need:g}+ years; you have {have:g}"
    return max(0.0, 1.0 + gap / 3.0), f"asks for {need:g}+ years; you have {have:g}"


def score_job(profile: Profile, job: Job, semantic: float, weights: dict[str, float] | None = None) -> MatchScore:
    w = weights or current_weights()
    skill, matched, missing = skill_component(profile, job)
    loc, loc_reason = location_component(profile, job)
    pref = preference_component(profile, job)
    exp, exp_reason = experience_component(profile, job)
    comps = ScoreComponents(semantic=min(1.0, max(0.0, semantic)), skill_overlap=skill,
                            location=loc, preferences=pref, experience=exp)
    total = sum(w[k] * getattr(comps, k) for k in COMPONENTS)
    reasons = []
    if matched:
        reasons.append(f"You have {len(matched)} of {len(matched) + len(missing)} listed skills: {', '.join(matched[:6])}")
    if missing:
        reasons.append(f"Missing: {', '.join(missing[:6])}")
    reasons.append(exp_reason[:1].upper() + exp_reason[1:])
    reasons.append(loc_reason[:1].upper() + loc_reason[1:])
    return MatchScore(job_id=job.id, score=int(round(100 * total)), components=comps, weights=w,
                      matched_skills=matched, missing_skills=missing, reasons=reasons)
