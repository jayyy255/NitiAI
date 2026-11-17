"""Pydantic models shared by the API, scoring and explanation layers."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class Profile(BaseModel):
    user_id: str | None = None
    title: str | None = None
    summary: str | None = None
    skills: list[str] = Field(default_factory=list)
    years_experience: float | None = None
    location: str | None = None
    work_mode: Literal["remote", "onsite", "hybrid"] | None = None
    preferred_roles: list[str] = Field(default_factory=list)
    preferred_locations: list[str] = Field(default_factory=list)
    experience_text: str = ""  # concatenated experience/project descriptions (used for grounding)

    def as_text(self) -> str:
        parts = [self.title or "", " ".join(self.preferred_roles), self.summary or "",
                 "Skills: " + ", ".join(self.skills), self.experience_text]
        return "\n".join(p for p in parts if p).strip()


class Job(BaseModel):
    id: str
    title: str
    company: str | None = None
    location: str | None = None
    remote: bool = False
    description: str = ""
    url: str | None = None
    salary_text: str | None = None
    salary_min: float | None = None
    salary_max: float | None = None
    skills: list[str] = Field(default_factory=list)
    min_years: float | None = None
    posted_at: datetime | None = None
    source: str | None = None

    def as_text(self) -> str:
        return f"{self.title}\n{self.company or ''} — {self.location or ''}\nSkills: {', '.join(self.skills)}\n{self.description}"


class ScoreComponents(BaseModel):
    semantic: float = Field(ge=0, le=1, description="Cross-encoder / embedding relevance, 0-1")
    skill_overlap: float = Field(ge=0, le=1, description="Share of the job's skills the candidate has")
    location: float = Field(ge=0, le=1)
    preferences: float = Field(ge=0, le=1)
    experience: float = Field(ge=0, le=1)


class MatchScore(BaseModel):
    job_id: str
    score: int = Field(ge=0, le=100)
    components: ScoreComponents
    weights: dict[str, float]
    matched_skills: list[str]
    missing_skills: list[str]
    reasons: list[str]


class ScoredJob(BaseModel):
    job: Job
    match: MatchScore
    retrieval: dict[str, float | int | None] = Field(default_factory=dict)


# ---- grounded explanation (LLM output, validated) ----

class Evidence(BaseModel):
    skill: str
    profile_quote: str = Field(description="Exact substring of the candidate profile")
    job_quote: str = Field(description="Exact substring of the job posting")


class MatchExplanation(BaseModel):
    job_id: str
    score: int = Field(ge=0, le=100)
    headline: str = Field(max_length=200)
    strengths: list[Evidence] = Field(default_factory=list, max_length=8)
    gaps: list[str] = Field(default_factory=list, max_length=8)
    concerns: list[str] = Field(default_factory=list, max_length=5)
    grounded: bool = True
    generator: Literal["llm", "template"] = "template"
    dropped_claims: int = 0


class SearchRequest(BaseModel):
    user_id: str | None = None
    profile: Profile | None = None
    query: str | None = None
    k: int = Field(default=20, ge=1, le=100)
    mode: Literal["bm25", "vector", "hybrid", "hybrid_rerank"] = "hybrid_rerank"
    location: str | None = None
    remote_only: bool = False


class ScoreRequest(BaseModel):
    profile: Profile | None = None
    user_id: str | None = None
    jobs: list[Job]


class RawJob(BaseModel):
    """Job as received from a source before normalisation."""
    source: str
    external_id: str | None = None
    title: str
    company: str | None = None
    location: str | None = None
    description: str = ""
    url: str | None = None
    salary_text: str | None = None
    salary_min: float | None = None
    salary_max: float | None = None
    posted_at: datetime | None = None
    remote: bool | None = None


class InteractionEvent(BaseModel):
    user_id: str
    job_id: str
    event: Literal["impression", "click", "save", "apply", "dismiss"]
    position: int | None = None
    score: float | None = None
    features: dict | None = None


class AgentMessage(BaseModel):
    user_id: str
    message: str = Field(min_length=1, max_length=4000)
