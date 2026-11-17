"""Job sources. Each returns a list of RawJob; network errors are raised to the pipeline."""
from __future__ import annotations

from datetime import datetime, timezone

import httpx

from ..config import get_settings
from ..models import RawJob


def _dt(v) -> datetime | None:
    if not v:
        return None
    if isinstance(v, (int, float)):
        return datetime.fromtimestamp(v / 1000 if v > 1e11 else v, tz=timezone.utc)
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def parse_rapidapi_item(j: dict) -> RawJob | None:
    title = j.get("title") or j.get("job_title") or j.get("jobTitle")
    if not title:
        return None
    comp = j.get("company")
    company = (j.get("organization") or j.get("company_name") or j.get("companyName")
               or (comp.get("name") if isinstance(comp, dict) else comp))
    loc = j.get("location") or j.get("job_location") or j.get("formattedLocation")
    if isinstance(loc, dict):
        loc = loc.get("city") or loc.get("name")
    if not loc and j.get("locations_derived"):
        loc = j["locations_derived"][0]
    salary = j.get("salary_raw") or j.get("salary")
    salary_text = salary if isinstance(salary, str) else (salary or {}).get("text") if isinstance(salary, dict) else None
    return RawJob(
        source="rapidapi",
        external_id=str(j.get("id") or j.get("job_id") or j.get("jobId") or "") or None,
        title=title, company=company, location=loc,
        description=j.get("description_text") or j.get("description") or j.get("job_description") or "",
        url=j.get("url") or j.get("job_url") or j.get("linkedin_url") or j.get("apply_url"),
        salary_text=salary_text,
        posted_at=_dt(j.get("date_posted") or j.get("posted_date") or j.get("listedAt")),
        remote=bool(j.get("remote_derived")) if "remote_derived" in j else None,
    )


def fetch_rapidapi(query: str, location: str, limit: int = 50) -> list[RawJob]:
    s = get_settings()
    if not s.rapidapi_key:
        return []
    r = httpx.get(f"https://{s.rapidapi_host}/active-jb-7d",
                  params={"title_filter": f'"{query}"', "location_filter": f'"{location}"', "limit": str(limit),
                          "offset": "0", "description_type": "text"},
                  headers={"x-rapidapi-key": s.rapidapi_key, "x-rapidapi-host": s.rapidapi_host}, timeout=30)
    r.raise_for_status()
    data = r.json()
    items = data if isinstance(data, list) else data.get("data") or data.get("jobs") or data.get("results") or []
    return [x for x in (parse_rapidapi_item(i) for i in items) if x]


def parse_adzuna_item(j: dict) -> RawJob | None:
    if not j.get("title"):
        return None
    return RawJob(
        source="adzuna", external_id=str(j.get("id")) if j.get("id") else None,
        title=j["title"], company=(j.get("company") or {}).get("display_name"),
        location=(j.get("location") or {}).get("display_name"), description=j.get("description") or "",
        url=j.get("redirect_url"), salary_min=j.get("salary_min"), salary_max=j.get("salary_max"),
        posted_at=_dt(j.get("created")),
    )


def fetch_adzuna(query: str, pages: int = 2, per_page: int = 50) -> list[RawJob]:
    s = get_settings()
    if not (s.adzuna_app_id and s.adzuna_app_key):
        return []
    out: list[RawJob] = []
    for page in range(1, pages + 1):
        r = httpx.get(f"https://api.adzuna.com/v1/api/jobs/{s.adzuna_country}/search/{page}",
                      params={"app_id": s.adzuna_app_id, "app_key": s.adzuna_app_key, "what": query,
                              "results_per_page": per_page, "max_days_old": s.max_posting_age_days,
                              "content-type": "application/json"}, timeout=30)
        r.raise_for_status()
        results = r.json().get("results", [])
        out += [x for x in (parse_adzuna_item(i) for i in results) if x]
        if len(results) < per_page:
            break
    return out
