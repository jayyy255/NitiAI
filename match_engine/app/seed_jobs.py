"""Generate and load a realistic MOCK job dataset (source = "seed").

Use this when no live job API (RapidAPI / Adzuna) is available. Every row is fictional:
company names are real-sounding brands used only as labels, salaries and requirements are
synthetic. Rows go through the normal ingestion pipeline (dedup, skill extraction, embedding).

  python -m app.seed_jobs                 # ~600 jobs into DATABASE_URL
  python -m app.seed_jobs --count 1200 --seed 7
  python -m app.seed_jobs --dry-run       # print a sample, touch nothing
  python -m app.seed_jobs --purge         # delete previously seeded jobs first

Seeded rows get last_seen_at = now(); re-run the script (or schedule it) to keep them from being
expired as stale after STALE_AFTER_DAYS.
"""
from __future__ import annotations

import argparse
import json
import random
from datetime import datetime, timedelta, timezone

from .models import RawJob

COMPANIES = {
    # name: (sector, size)
    "Razorpay": ("fintech", "scaleup"), "PhonePe": ("fintech", "large"), "Paytm": ("fintech", "large"),
    "Zerodha": ("fintech", "scaleup"), "Groww": ("fintech", "scaleup"), "CRED": ("fintech", "scaleup"),
    "Juspay": ("fintech", "scaleup"), "Slice": ("fintech", "startup"), "Jupiter": ("fintech", "startup"),
    "BharatPe": ("fintech", "scaleup"), "Pine Labs": ("fintech", "large"), "Cashfree": ("fintech", "startup"),
    "Flipkart": ("ecommerce", "large"), "Meesho": ("ecommerce", "scaleup"), "Myntra": ("ecommerce", "large"),
    "Nykaa": ("ecommerce", "large"), "Swiggy": ("consumer", "large"), "Zomato": ("consumer", "large"),
    "Zepto": ("consumer", "scaleup"), "Blinkit": ("consumer", "scaleup"), "Dunzo": ("consumer", "startup"),
    "Urban Company": ("consumer", "scaleup"), "Ola": ("mobility", "large"), "Rapido": ("mobility", "scaleup"),
    "Ather Energy": ("mobility", "scaleup"), "Ola Electric": ("mobility", "large"),
    "Freshworks": ("saas", "large"), "Zoho": ("saas", "large"), "Postman": ("saas", "scaleup"),
    "BrowserStack": ("saas", "scaleup"), "Chargebee": ("saas", "scaleup"), "Hasura": ("saas", "startup"),
    "Atlan": ("saas", "startup"), "Druva": ("saas", "scaleup"), "LeadSquared": ("saas", "scaleup"),
    "Darwinbox": ("saas", "scaleup"), "Whatfix": ("saas", "scaleup"), "Innovaccer": ("healthtech", "scaleup"),
    "Practo": ("healthtech", "scaleup"), "PharmEasy": ("healthtech", "scaleup"), "CureFit": ("healthtech", "scaleup"),
    "Unacademy": ("edtech", "scaleup"), "upGrad": ("edtech", "scaleup"), "PhysicsWallah": ("edtech", "scaleup"),
    "Scaler": ("edtech", "startup"), "Sarvam AI": ("ai", "startup"), "Krutrim": ("ai", "scaleup"),
    "Haptik": ("ai", "scaleup"), "Uniphore": ("ai", "scaleup"), "Observe.AI": ("ai", "scaleup"),
    "Fractal Analytics": ("analytics", "large"), "Mu Sigma": ("analytics", "large"), "Tiger Analytics": ("analytics", "large"),
    "Google": ("bigtech", "large"), "Microsoft": ("bigtech", "large"), "Amazon": ("bigtech", "large"),
    "Atlassian": ("bigtech", "large"), "Adobe": ("bigtech", "large"), "Salesforce": ("bigtech", "large"),
    "Uber": ("bigtech", "large"), "Intuit": ("bigtech", "large"), "Walmart Global Tech": ("bigtech", "large"),
    "Goldman Sachs": ("finance", "large"), "JPMorgan Chase": ("finance", "large"), "Morgan Stanley": ("finance", "large"),
    "Tower Research": ("finance", "scaleup"), "Graviton Research": ("finance", "startup"),
    "TCS": ("services", "large"), "Infosys": ("services", "large"), "Wipro": ("services", "large"),
    "HCLTech": ("services", "large"), "Tech Mahindra": ("services", "large"), "Accenture": ("services", "large"),
    "Capgemini": ("services", "large"), "Cognizant": ("services", "large"), "LTIMindtree": ("services", "large"),
    "Persistent Systems": ("services", "large"), "Thoughtworks": ("services", "large"), "Deloitte": ("consulting", "large"),
    "EY": ("consulting", "large"), "KPMG": ("consulting", "large"), "Bosch": ("automotive", "large"),
    "Tata Elxsi": ("automotive", "large"), "Qualcomm": ("semiconductor", "large"), "NVIDIA": ("semiconductor", "large"),
    "Intel": ("semiconductor", "large"), "Samsung R&D": ("semiconductor", "large"), "CrowdStrike": ("security", "large"),
    "Palo Alto Networks": ("security", "large"), "Quick Heal": ("security", "scaleup"), "Rippling": ("saas", "scaleup"),
    "Gojek": ("mobility", "large"), "Dream11": ("gaming", "scaleup"), "MPL": ("gaming", "scaleup"),
    "Games24x7": ("gaming", "scaleup"), "ShareChat": ("social", "scaleup"), "InMobi": ("adtech", "large"),
    "Glance": ("adtech", "scaleup"), "Delhivery": ("logistics", "large"), "Shiprocket": ("logistics", "scaleup"),
    "Rivigo": ("logistics", "startup"), "Licious": ("consumer", "scaleup"), "boAt": ("consumer", "scaleup"),
    "Lenskart": ("ecommerce", "scaleup"), "IndiaMART": ("ecommerce", "large"), "Udaan": ("ecommerce", "scaleup"),
}

CITIES = ["Bengaluru, India", "Hyderabad, India", "Pune, India", "Mumbai, India", "Chennai, India",
          "Gurugram, India", "Noida, India", "Delhi, India", "Kolkata, India", "Ahmedabad, India", "Kochi, India"]
CITY_WEIGHTS = [30, 14, 12, 12, 9, 9, 5, 3, 2, 2, 2]

# role family: (title variants, core skills, optional skills, base salary LPA at 0 yrs, sectors it fits ('*' = all))
ROLES = {
    "backend": (["Backend Engineer", "Backend Developer", "Software Engineer - Backend", "Server-side Engineer"],
                ["REST APIs", "SQL", "Git"],
                ["Node.js", "Express", "Python", "Django", "FastAPI", "Java", "Spring Boot", "Go", "PostgreSQL", "MySQL",
                 "MongoDB", "Redis", "Kafka", "Docker", "Kubernetes", "AWS", "Microservices", "GraphQL"], 8, "*"),
    "frontend": (["Frontend Engineer", "Frontend Developer", "UI Engineer", "Software Engineer - Frontend"],
                 ["JavaScript", "HTML", "CSS"],
                 ["React", "TypeScript", "Next.js", "Redux", "Vue", "Angular", "Tailwind CSS", "Unit Testing", "GraphQL", "Figma"], 7, "*"),
    "fullstack": (["Full Stack Developer", "Full Stack Engineer", "Software Engineer - Full Stack", "MERN Stack Developer"],
                  ["JavaScript", "REST APIs", "Git"],
                  ["React", "Node.js", "Express", "TypeScript", "Next.js", "MongoDB", "PostgreSQL", "Docker", "AWS", "GraphQL"], 8, "*"),
    "mobile": (["Android Developer", "iOS Developer", "Mobile Engineer", "Flutter Developer", "React Native Developer"],
               ["Git", "REST APIs"],
               ["Kotlin", "Java", "Swift", "Flutter", "Dart", "React Native", "TypeScript", "Unit Testing"], 7,
               ["fintech", "consumer", "ecommerce", "mobility", "social", "gaming", "edtech", "healthtech"]),
    "data_analyst": (["Data Analyst", "Business Analyst", "Product Analyst", "BI Analyst"],
                     ["SQL", "Excel", "Data Analysis"],
                     ["Python", "Tableau", "Power BI", "Statistics", "Pandas", "BigQuery"], 5, "*"),
    "data_scientist": (["Data Scientist", "Applied Scientist", "Decision Scientist"],
                       ["Python", "SQL", "Statistics", "Machine Learning"],
                       ["Pandas", "scikit-learn", "Spark", "Deep Learning", "TensorFlow", "PyTorch", "NLP"], 10,
                       ["fintech", "ecommerce", "consumer", "analytics", "bigtech", "healthtech", "adtech", "ai", "finance"]),
    "data_engineer": (["Data Engineer", "Big Data Engineer", "Analytics Engineer", "Data Platform Engineer"],
                      ["Python", "SQL", "ETL"],
                      ["Spark", "Airflow", "Kafka", "dbt", "Snowflake", "BigQuery", "AWS", "Hadoop", "Scala"], 9, "*"),
    "ml_engineer": (["Machine Learning Engineer", "ML Engineer", "AI Engineer", "NLP Engineer", "Computer Vision Engineer", "MLOps Engineer"],
                    ["Python", "Machine Learning"],
                    ["PyTorch", "TensorFlow", "Deep Learning", "NLP", "LLMs", "RAG", "Computer Vision", "MLOps", "Docker",
                     "Kubernetes", "FastAPI", "Vector Databases"], 12,
                    ["ai", "bigtech", "ecommerce", "fintech", "analytics", "healthtech", "mobility", "semiconductor", "adtech"]),
    "devops": (["DevOps Engineer", "Site Reliability Engineer", "Cloud Engineer", "Platform Engineer"],
               ["Linux", "Docker", "CI/CD"],
               ["Kubernetes", "AWS", "GCP", "Azure", "Terraform", "Ansible", "Jenkins", "GitHub Actions", "Prometheus",
                "Grafana", "Bash", "Go"], 9, "*"),
    "qa": (["QA Engineer", "SDET", "Test Automation Engineer"],
           ["Unit Testing", "Git"], ["Java", "Python", "JavaScript", "CI/CD", "Agile", "REST APIs"], 6, "*"),
    "security": (["Security Engineer", "Application Security Engineer", "SOC Analyst", "Cloud Security Engineer"],
                 ["Cybersecurity", "Linux", "Networking"], ["Python", "AWS", "Azure", "Bash"], 9,
                 ["security", "fintech", "bigtech", "finance", "services", "consulting", "saas"]),
    "embedded": (["Embedded Software Engineer", "Firmware Engineer", "Systems Software Engineer"],
                 ["C", "C++", "Linux"], ["Python", "Networking", "Data Structures & Algorithms"], 8,
                 ["automotive", "semiconductor", "mobility"]),
    "lowlatency": (["C++ Developer", "Quantitative Developer", "Low Latency Engineer"],
                   ["C++", "Data Structures & Algorithms", "Linux"], ["Python", "Networking", "Statistics"], 20, ["finance"]),
    "sde_newgrad": (["Software Development Engineer I", "Graduate Engineer Trainee", "Associate Software Engineer", "Software Engineer - New Grad"],
                    ["Data Structures & Algorithms", "Git"], ["Java", "C++", "Python", "SQL", "System Design", "JavaScript"], 6, "*"),
    "product": (["Product Manager", "Associate Product Manager", "Technical Product Manager"],
                ["Communication", "Data Analysis"], ["SQL", "Agile", "Leadership", "Problem Solving", "Figma"], 12,
                ["fintech", "ecommerce", "consumer", "saas", "edtech", "healthtech", "bigtech", "mobility"]),
    "design": (["Product Designer", "UI/UX Designer", "UX Researcher"],
               ["Figma", "UI/UX"], ["Communication", "HTML", "CSS"], 7,
               ["fintech", "ecommerce", "consumer", "saas", "edtech", "healthtech", "bigtech", "gaming"]),
}

LEVELS = [  # (prefix, min_years, max_years, weight, salary multiplier)
    ("Intern", 0, 0, 8, 0.35), ("", 0, 1, 22, 1.0), ("", 1, 3, 30, 1.5), ("Senior", 3, 6, 25, 2.4),
    ("Staff", 7, 10, 7, 3.6), ("Lead", 6, 9, 8, 3.2),
]
SIZE_PAY = {"startup": 0.9, "scaleup": 1.15, "large": 1.0}
SECTOR_PAY = {"bigtech": 1.5, "finance": 1.6, "services": 0.65, "consulting": 0.8, "ai": 1.25}

OPENERS = [
    "{company} is hiring a {title} to join its {team} team.",
    "Join {company}'s {team} team as a {title}.",
    "We're looking for a {title} who wants to own meaningful problems in {team}.",
    "As a {title} at {company}, you will help build our {team} platform.",
    "{company} ({sector}) is growing its {team} team and needs a {title}.",
]
WORK = [
    "You'll design, build and ship features end to end and work closely with product and design.",
    "You'll own services in production, write clean, tested code and take part in code reviews.",
    "You'll turn ambiguous problems into well-scoped projects and see them through to launch.",
    "You'll improve reliability and performance of systems used by millions of customers.",
    "You'll collaborate with a small, senior team and have real ownership from week one.",
]
PERKS = ["Health insurance for you and family.", "Flexible working hours.", "Learning budget and conference passes.",
         "ESOPs.", "Hybrid work: 3 days in office.", "Relocation support.", "Wellness allowance."]
TEAMS = {"backend": ["Payments", "Platform", "Core Services", "Checkout", "Identity"],
         "frontend": ["Web", "Growth", "Design Systems", "Consumer Web"],
         "fullstack": ["Growth", "Internal Tools", "Merchant", "Creator"],
         "mobile": ["Mobile", "Consumer App", "Driver App"],
         "data_analyst": ["Analytics", "Business Intelligence", "Growth Analytics"],
         "data_scientist": ["Data Science", "Risk", "Pricing", "Recommendations"],
         "data_engineer": ["Data Platform", "Data Engineering", "Analytics Infrastructure"],
         "ml_engineer": ["Applied ML", "Search & Ranking", "GenAI", "Vision", "Personalisation"],
         "devops": ["Infrastructure", "SRE", "Cloud Platform"], "qa": ["Quality Engineering", "Release"],
         "security": ["Security", "Trust & Safety", "Detection & Response"], "embedded": ["Firmware", "Embedded Systems"],
         "lowlatency": ["Trading Systems", "Execution"], "sde_newgrad": ["Engineering"], "product": ["Product"],
         "design": ["Design"]}


ENGINEERING = {"backend", "frontend", "fullstack", "mobile", "data_engineer", "ml_engineer", "devops", "embedded", "lowlatency"}


def _pick_skills(rng: random.Random, core: list[str], optional: list[str], level_idx: int, family: str) -> tuple[list[str], list[str]]:
    must = list(core) + rng.sample(optional, k=min(len(optional), rng.randint(1, 3)))
    nice = [s for s in rng.sample(optional, k=min(len(optional), rng.randint(1, 3))) if s not in must]
    if family in ENGINEERING and level_idx >= 3 and "System Design" not in must and rng.random() < 0.5:
        must.append("System Design")
    return must, nice


def generate(count: int = 600, seed: int = 42) -> list[RawJob]:
    rng = random.Random(seed)
    now = datetime.now(timezone.utc)
    companies = list(COMPANIES.items())
    out: list[RawJob] = []
    seen: set[tuple] = set()
    attempts = 0
    while len(out) < count and attempts < count * 20:
        attempts += 1
        company, (sector, size) = rng.choice(companies)
        family = rng.choice([f for f, r in ROLES.items() if r[4] == "*" or sector in r[4]])
        titles, core, optional, base, _ = ROLES[family]
        if family in ("sde_newgrad",):
            lvl_idx = 1
        else:
            lvl_idx = rng.choices(range(len(LEVELS)), weights=[l[3] for l in LEVELS])[0]
        prefix, ymin, ymax, _, mult = LEVELS[lvl_idx]
        base_title = rng.choice(titles)
        if prefix == "Intern":
            title = f"{base_title} Intern"
        elif prefix and not base_title.startswith(("Associate", "Graduate", "Software Development Engineer I")):
            title = f"{prefix} {base_title}"
        else:
            title = base_title
        remote = rng.random() < (0.25 if size == "startup" else 0.1)
        city = "Remote, India" if remote else rng.choices(CITIES, weights=CITY_WEIGHTS)[0]
        key = (company, title, city)
        if key in seen:
            continue
        seen.add(key)

        must, nice = _pick_skills(rng, core, optional, lvl_idx, family)
        team = rng.choice(TEAMS[family])
        years_txt = ("Open to students and freshers." if ymax == 0 else
                     "Freshers and candidates with up to 1 year of experience are welcome." if ymin == 0 else
                     f"Requires {ymin}+ years of experience.")
        pay = base * mult * SIZE_PAY[size] * SECTOR_PAY.get(sector, 1.0) * rng.uniform(0.85, 1.2)
        if prefix == "Intern":
            salary_text = f"₹{int(pay * 100000 / 12 / 1000) * 1000:,} per month stipend"
            smin = smax = None
        else:
            smin, smax = round(pay, 1), round(pay * rng.uniform(1.2, 1.5), 1)
            salary_text = f"₹{smin}L - ₹{smax}L per annum"
        desc = " ".join([
            rng.choice(OPENERS).format(company=company, title=title, team=team, sector=sector),
            rng.choice(WORK),
            years_txt,
            f"Must have: {', '.join(must)}.",
            (f"Nice to have: {', '.join(nice)}." if nice else ""),
            " ".join(rng.sample(PERKS, k=2)),
        ]).strip()
        out.append(RawJob(
            source="seed", external_id=f"seed-{seed}-{len(out):05d}", title=title, company=company, location=city,
            description=desc, url=None, salary_text=salary_text,
            salary_min=smin * 100000 if smin else None, salary_max=smax * 100000 if smax else None,
            posted_at=now - timedelta(days=rng.randint(0, 20), hours=rng.randint(0, 23)), remote=remote,
        ))
    return out


def main():
    ap = argparse.ArgumentParser(description="Load a MOCK job dataset (source='seed').")
    ap.add_argument("--count", type=int, default=600)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--purge", action="store_true", help="delete existing source='seed' jobs first")
    args = ap.parse_args()

    jobs = generate(args.count, args.seed)
    if args.dry_run:
        for j in jobs[:3]:
            print(json.dumps(j.model_dump(mode="json"), indent=1))
        print(f"... {len(jobs)} jobs, {len({j.company for j in jobs})} companies")
        return

    from .db import conn, migrate
    from .ingest.pipeline import embed_pending, upsert

    migrate()
    if args.purge:
        with conn() as c:
            n = c.execute("DELETE FROM jobs WHERE source = 'seed'").rowcount
        print(f"purged {n} seeded jobs")
    stats = upsert(jobs)
    embedded = embed_pending()
    print(json.dumps({"generated": len(jobs), "companies": len({j.company for j in jobs}),
                      "inserted": stats["inserted"], "updated": stats["updated"], "embedded": embedded}))


if __name__ == "__main__":
    main()
