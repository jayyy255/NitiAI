"""Grounded match explanations.

The LLM may only cite evidence that literally appears in the profile and the posting.
Output is validated with Pydantic, then every strength is checked: both quotes must be
substrings of their source texts and must mention the skill. Claims that fail are dropped
(counted in `dropped_claims`). With no LLM available, a deterministic template is used.
"""
from __future__ import annotations

import re

from pydantic import ValidationError

from . import llm
from .models import Evidence, Job, MatchExplanation, MatchScore, Profile
from .skills import TAXONOMY, extract_skill_hits

_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "headline": {"type": "STRING"},
        "strengths": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "skill": {"type": "STRING"}, "profile_quote": {"type": "STRING"}, "job_quote": {"type": "STRING"}},
            "required": ["skill", "profile_quote", "job_quote"]}},
        "gaps": {"type": "ARRAY", "items": {"type": "STRING"}},
        "concerns": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": ["headline", "strengths", "gaps", "concerns"],
}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


def _mentions(quote: str, skill: str) -> bool:
    q = _norm(quote)
    forms = {skill.lower(), *[a.lower() for a in TAXONOMY.get(skill, [])]}
    return any(f in q for f in forms)


def verify_evidence(ev: Evidence, profile_text: str, job_text: str) -> bool:
    pq, jq = _norm(ev.profile_quote), _norm(ev.job_quote)
    return (bool(pq) and bool(jq) and pq in _norm(profile_text) and jq in _norm(job_text)
            and _mentions(ev.profile_quote, ev.skill) and _mentions(ev.job_quote, ev.skill))


def _snippet(text: str, start: int, end: int, pad: int = 40) -> str:
    a = max(0, start - pad)
    b = min(len(text), end + pad)
    while a > 0 and not text[a - 1].isspace():
        a -= 1
    while b < len(text) and not text[b].isspace():
        b += 1
    return text[a:b].strip()


def template_explanation(profile: Profile, job: Job, match: MatchScore) -> MatchExplanation:
    ptext, jtext = profile.as_text(), job.as_text()
    phits = {h.skill: h for h in extract_skill_hits(ptext)}
    jhits = {h.skill: h for h in extract_skill_hits(jtext)}
    strengths = []
    for sk in match.matched_skills:
        if sk in phits and sk in jhits:
            ph, jh = phits[sk], jhits[sk]
            strengths.append(Evidence(skill=sk, profile_quote=_snippet(ptext, ph.start, ph.end),
                                      job_quote=_snippet(jtext, jh.start, jh.end)))
    c = match.components
    concerns = []
    if c.experience < 0.7:
        concerns.append(next((r for r in match.reasons if "years" in r), "Experience requirement may not be met"))
    if c.location < 0.5:
        concerns.append(next((r for r in match.reasons if "remote" in r.lower() or "located" in r.lower()), "Location mismatch"))
    headline = (f"{match.score}/100 for {job.title}"
                + (f" at {job.company}" if job.company else "")
                + f": {len(match.matched_skills)} matching skills, {len(match.missing_skills)} to learn")
    return MatchExplanation(job_id=job.id, score=match.score, headline=headline[:200], strengths=strengths[:8],
                            gaps=match.missing_skills[:8], concerns=concerns, generator="template")


def _prompt(profile: Profile, job: Job, match: MatchScore) -> str:
    return f"""You explain why a candidate does or does not fit a job. Use ONLY the two texts below.
Rules:
- Every strength must quote an exact, verbatim substring of the CANDIDATE text and of the JOB text, each mentioning the skill.
- Do not invent employers, years, degrees or skills. If unsure, leave it out.
- gaps: skills the job asks for that the candidate text does not show. concerns: experience/location issues stated in the texts.
- headline: one sentence, no more than 25 words, consistent with the score {match.score}/100.

Computed signals (already verified): matched={match.matched_skills}; missing={match.missing_skills}; reasons={match.reasons}

CANDIDATE:
<<<{profile.as_text()[:5000]}>>>

JOB:
<<<{job.as_text()[:5000]}>>>"""


def explain(profile: Profile, job: Job, match: MatchScore, use_llm: bool = True) -> MatchExplanation:
    if not (use_llm and llm.available()):
        return template_explanation(profile, job, match)
    try:
        raw = llm.generate_json(_prompt(profile, job, match), _SCHEMA)
        raw.update(job_id=job.id, score=match.score, generator="llm")
        raw["headline"] = str(raw.get("headline", ""))[:200]
        raw["strengths"] = (raw.get("strengths") or [])[:8]
        raw["gaps"] = (raw.get("gaps") or [])[:8]
        raw["concerns"] = (raw.get("concerns") or [])[:5]
        exp = MatchExplanation.model_validate(raw)
    except (llm.LLMUnavailable, ValidationError, ValueError, KeyError):
        return template_explanation(profile, job, match)

    ptext, jtext = profile.as_text(), job.as_text()
    kept = [e for e in exp.strengths if verify_evidence(e, ptext, jtext)]
    dropped = len(exp.strengths) - len(kept)
    # gaps must be skills the job actually mentions and the profile does not
    pskills = {h.skill for h in extract_skill_hits(ptext)}
    jskills = {h.skill for h in extract_skill_hits(jtext)}
    gaps = [g for g in exp.gaps if (g in jskills and g not in pskills) or g in match.missing_skills]
    dropped += len(exp.gaps) - len(gaps)
    return exp.model_copy(update={"strengths": kept, "gaps": gaps, "dropped_claims": dropped, "grounded": True})
