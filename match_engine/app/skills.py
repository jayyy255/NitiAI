"""Skill taxonomy and deterministic skill extraction.

Every skill has a canonical name and a list of surface forms. Extraction is a
word-boundary regex match, so results are explainable: each extracted skill can
point at the exact text span it came from.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

# canonical -> aliases (lower-case). The canonical name is always an alias of itself.
TAXONOMY: dict[str, list[str]] = {
    # languages
    "Python": ["python", "python3"],
    "Java": ["java"],
    "JavaScript": ["javascript", "js", "ecmascript"],
    "TypeScript": ["typescript", "ts"],
    "C++": ["c++", "cpp"],
    "C#": ["c#", "csharp", ".net", "dotnet"],
    "C": ["c programming", "ansi c"],
    "Go": ["golang", "go lang"],
    "Rust": ["rust"],
    "Kotlin": ["kotlin"],
    "Swift": ["swift"],
    "Ruby": ["ruby"],
    "PHP": ["php"],
    "Scala": ["scala"],
    "R": ["r programming", "rstudio", "r language"],
    "SQL": ["sql", "t-sql", "pl/sql", "plsql"],
    "Bash": ["bash", "shell scripting", "shell script"],
    "Dart": ["dart"],
    # web / frontend
    "React": ["react", "react.js", "reactjs"],
    "Next.js": ["next.js", "nextjs"],
    "Angular": ["angular", "angularjs"],
    "Vue": ["vue", "vue.js", "vuejs"],
    "HTML": ["html", "html5"],
    "CSS": ["css", "css3", "scss", "sass"],
    "Tailwind CSS": ["tailwind", "tailwindcss", "tailwind css"],
    "Redux": ["redux"],
    "Flutter": ["flutter"],
    "React Native": ["react native"],
    # backend
    "Node.js": ["node.js", "nodejs", "node"],
    "Express": ["express", "express.js", "expressjs"],
    "Django": ["django"],
    "Flask": ["flask"],
    "FastAPI": ["fastapi"],
    "Spring Boot": ["spring boot", "springboot", "spring"],
    "REST APIs": ["rest", "restful", "rest api", "rest apis", "restful apis"],
    "GraphQL": ["graphql"],
    "gRPC": ["grpc"],
    "Microservices": ["microservices", "microservice"],
    # data stores
    "PostgreSQL": ["postgresql", "postgres"],
    "MySQL": ["mysql"],
    "MongoDB": ["mongodb", "mongo"],
    "Redis": ["redis"],
    "Elasticsearch": ["elasticsearch", "elastic search", "opensearch"],
    "Cassandra": ["cassandra"],
    "DynamoDB": ["dynamodb"],
    "Snowflake": ["snowflake"],
    "BigQuery": ["bigquery"],
    # cloud / devops
    "AWS": ["aws", "amazon web services", "ec2", "s3", "lambda"],
    "GCP": ["gcp", "google cloud", "google cloud platform"],
    "Azure": ["azure", "microsoft azure"],
    "Docker": ["docker", "containers", "containerization"],
    "Kubernetes": ["kubernetes", "k8s", "eks", "gke", "aks"],
    "Terraform": ["terraform"],
    "Ansible": ["ansible"],
    "CI/CD": ["ci/cd", "cicd", "continuous integration", "continuous delivery", "continuous deployment"],
    "Jenkins": ["jenkins"],
    "GitHub Actions": ["github actions"],
    "Linux": ["linux", "unix"],
    "Git": ["git", "github", "gitlab", "version control"],
    "Prometheus": ["prometheus"],
    "Grafana": ["grafana"],
    # data / ml
    "Machine Learning": ["machine learning", "ml"],
    "Deep Learning": ["deep learning", "neural networks", "neural network"],
    "NLP": ["nlp", "natural language processing"],
    "Computer Vision": ["computer vision", "opencv", "image processing"],
    "LLMs": ["llm", "llms", "large language models", "large language model", "generative ai", "genai"],
    "RAG": ["rag", "retrieval augmented generation", "retrieval-augmented generation"],
    "TensorFlow": ["tensorflow", "keras"],
    "PyTorch": ["pytorch", "torch"],
    "scikit-learn": ["scikit-learn", "sklearn", "scikit learn"],
    "Pandas": ["pandas"],
    "NumPy": ["numpy"],
    "Spark": ["spark", "pyspark", "apache spark"],
    "Hadoop": ["hadoop"],
    "Kafka": ["kafka", "apache kafka"],
    "Airflow": ["airflow", "apache airflow"],
    "dbt": ["dbt"],
    "ETL": ["etl", "elt", "data pipelines", "data pipeline"],
    "Data Analysis": ["data analysis", "data analytics", "analytics"],
    "Statistics": ["statistics", "statistical", "hypothesis testing", "a/b testing"],
    "Tableau": ["tableau"],
    "Power BI": ["power bi", "powerbi"],
    "Excel": ["excel", "spreadsheets"],
    "MLOps": ["mlops"],
    "Vector Databases": ["vector database", "vector databases", "pgvector", "pinecone", "faiss", "weaviate"],
    # testing / practice
    "Unit Testing": ["unit testing", "unit tests", "jest", "pytest", "junit", "tdd"],
    "System Design": ["system design", "distributed systems", "scalability"],
    "Data Structures & Algorithms": ["data structures", "algorithms", "dsa"],
    "Agile": ["agile", "scrum", "kanban"],
    "Figma": ["figma"],
    "UI/UX": ["ui/ux", "ux design", "ui design", "user experience"],
    "Cybersecurity": ["cybersecurity", "security", "penetration testing", "siem", "owasp"],
    "Networking": ["networking", "tcp/ip", "dns"],
    # soft
    "Communication": ["communication", "communication skills"],
    "Leadership": ["leadership", "mentoring", "team lead"],
    "Problem Solving": ["problem solving", "problem-solving"],
}

# Aliases that are common English words / ambiguous: only matched in a strongly technical context
# (i.e. when the original casing is used or when they appear in a comma/slash separated list).
AMBIGUOUS = {"go", "r", "c", "rust", "swift", "spring", "express", "node", "security", "analytics",
             "rest", "ml", "ts", "js", "rag", "lambda", "containers", "torch", "dart", "scala"}


@dataclass(frozen=True)
class SkillHit:
    skill: str
    alias: str
    start: int
    end: int


@lru_cache(maxsize=1)
def _compiled() -> list[tuple[str, str, re.Pattern]]:
    out = []
    for canon, aliases in TAXONOMY.items():
        for alias in {a.lower() for a in aliases} | {canon.lower()}:
            # word boundary that also works for tokens like c++, c#, .net, ci/cd
            pat = re.compile(r"(?<![\w+#./-])" + re.escape(alias) + r"(?![\w+#-]|\.\w)", re.IGNORECASE)
            out.append((canon, alias, pat))
    # longest aliases first so "react native" wins over "react"
    out.sort(key=lambda t: -len(t[1]))
    return out


def _ambiguous_ok(text: str, alias: str, start: int, end: int) -> bool:
    surface = text[start:end]
    if alias in {"go", "r", "c"}:
        # require exact upper-case surface ("Go", "R", "C") and a list-like neighbourhood
        if surface not in {"Go", "R", "C"}:
            return False
    window = text[max(0, start - 3):min(len(text), end + 3)]
    listy = any(ch in window for ch in ",/|•;()")
    capitalised = surface[:1].isupper() or surface.isupper()
    return listy or capitalised


def extract_skill_hits(text: str) -> list[SkillHit]:
    if not text:
        return []
    taken: list[tuple[int, int]] = []
    hits: list[SkillHit] = []
    for canon, alias, pat in _compiled():
        for m in pat.finditer(text):
            s, e = m.span()
            if any(s < te and e > ts for ts, te in taken):
                continue
            if alias in AMBIGUOUS and not _ambiguous_ok(text, alias, s, e):
                continue
            taken.append((s, e))
            hits.append(SkillHit(canon, alias, s, e))
    hits.sort(key=lambda h: h.start)
    return hits


def extract_skills(text: str) -> list[str]:
    seen: dict[str, None] = {}
    for h in extract_skill_hits(text):
        seen.setdefault(h.skill, None)
    return list(seen)


_CANON_LOOKUP: dict[str, str] = {}
for _c, _as in TAXONOMY.items():
    _CANON_LOOKUP[_c.lower()] = _c
    for _a in _as:
        _CANON_LOOKUP.setdefault(_a.lower(), _c)


def canonicalize(skill: str) -> str:
    """Map a free-form skill string (e.g. from the resume parser) to its canonical name."""
    s = (skill or "").strip()
    if not s:
        return s
    hit = _CANON_LOOKUP.get(s.lower())
    if hit:
        return hit
    found = extract_skills(s)
    return found[0] if len(found) == 1 else s


def canonical_set(skills: list[str] | None) -> list[str]:
    out: dict[str, None] = {}
    for s in skills or []:
        c = canonicalize(s)
        if c:
            out.setdefault(c, None)
    return list(out)


_YEARS_RE = re.compile(
    r"(\d{1,2})\s*(?:\+|plus)?\s*(?:-|–|to)?\s*(\d{1,2})?\s*\+?\s*(?:years?|yrs?)(?:\s+of)?(?:\s+\w+){0,3}?\s+(?:experience|exp)",
    re.IGNORECASE,
)
_FRESHER_RE = re.compile(r"\b(fresher|freshers|entry[- ]level|new grad|graduate trainee|0\s*-\s*1 years?)\b", re.I)


def parse_min_years(text: str) -> float | None:
    """Minimum years of experience a posting asks for, or None if it does not say."""
    if not text:
        return None
    if _FRESHER_RE.search(text):
        return 0.0
    vals = [int(m.group(1)) for m in _YEARS_RE.finditer(text) if int(m.group(1)) <= 25]
    return float(min(vals)) if vals else None
