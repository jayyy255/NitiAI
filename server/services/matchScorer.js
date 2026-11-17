// Deterministic fallback for when the Python Match Engine is unreachable.
// Same components and default weights as match_engine/app/scoring.py, except that "semantic"
// is a token-overlap cosine instead of a cross-encoder. Never random.
const WEIGHTS = { semantic: 0.35, skill_overlap: 0.35, location: 0.1, preferences: 0.1, experience: 0.1 };

// canonical -> aliases (subset of match_engine/app/skills.py covering the most common skills)
const TAXONOMY = {
  Python: ["python"], Java: ["java"], JavaScript: ["javascript"], TypeScript: ["typescript"], "C++": ["c++", "cpp"],
  "C#": ["c#", ".net", "dotnet"], Go: ["golang"], Kotlin: ["kotlin"], Swift: ["swift"], SQL: ["sql"], PHP: ["php"],
  React: ["react", "react.js", "reactjs"], "Next.js": ["next.js", "nextjs"], Angular: ["angular"], Vue: ["vue", "vue.js"],
  HTML: ["html", "html5"], CSS: ["css", "css3", "scss"], "Node.js": ["node.js", "nodejs"], Express: ["express.js", "expressjs"],
  Django: ["django"], Flask: ["flask"], FastAPI: ["fastapi"], "Spring Boot": ["spring boot", "springboot"],
  "REST APIs": ["rest api", "rest apis", "restful"], GraphQL: ["graphql"], Microservices: ["microservices"],
  PostgreSQL: ["postgresql", "postgres"], MySQL: ["mysql"], MongoDB: ["mongodb", "mongo"], Redis: ["redis"],
  AWS: ["aws", "amazon web services"], GCP: ["gcp", "google cloud"], Azure: ["azure"], Docker: ["docker"],
  Kubernetes: ["kubernetes", "k8s"], Terraform: ["terraform"], "CI/CD": ["ci/cd", "cicd"], Linux: ["linux"], Git: ["git", "github"],
  "Machine Learning": ["machine learning"], "Deep Learning": ["deep learning"], NLP: ["nlp", "natural language processing"],
  LLMs: ["llm", "llms", "generative ai", "genai"], TensorFlow: ["tensorflow"], PyTorch: ["pytorch"],
  "scikit-learn": ["scikit-learn", "sklearn"], Pandas: ["pandas"], Spark: ["spark", "pyspark"], Kafka: ["kafka"],
  Airflow: ["airflow"], Tableau: ["tableau"], "Power BI": ["power bi", "powerbi"], Excel: ["excel"],
  Flutter: ["flutter"], "React Native": ["react native"], Figma: ["figma"], Statistics: ["statistics"],
  "Data Analysis": ["data analysis", "data analytics"], "System Design": ["system design", "distributed systems"],
};

const LOOKUP = new Map();
for (const [canon, aliases] of Object.entries(TAXONOMY)) {
  LOOKUP.set(canon.toLowerCase(), canon);
  aliases.forEach((a) => LOOKUP.set(a, canon));
}
const escapeRe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
// longest aliases first so "react native" wins over "react"
const PATTERNS = [...LOOKUP.keys()]
  .sort((a, b) => b.length - a.length)
  .map((a) => [a, `(?<![\\w+#.-])${escapeRe(a)}(?![\\w+#-])`]);

function extractSkills(text) {
  const found = new Set();
  let t = String(text || "");
  for (const [alias, src] of PATTERNS) {
    const re = new RegExp(src, "gi");
    if (re.test(t)) {
      found.add(LOOKUP.get(alias));
      t = t.replace(new RegExp(src, "gi"), " ");
    }
  }
  return [...found];
}

const canonical = (skills) =>
  [...new Set((skills || []).map((s) => LOOKUP.get(String(s).trim().toLowerCase()) || String(s).trim()).filter(Boolean))];

const STOP = new Set(["and", "or", "the", "a", "of", "for", "in", "at", "to", "with"]);
const GENERIC = new Set(["engineer", "developer", "software", "senior", "junior", "lead", "intern", "associate", "trainee"]);
const tokens = (s) => new Set((String(s || "").toLowerCase().match(/[a-z0-9+#]+/g) || []).filter((t) => !STOP.has(t)));
const specific = (s) => {
  const all = tokens(s);
  const narrowed = new Set([...all].filter((t) => !GENERIC.has(t)));
  return narrowed.size ? narrowed : all;
};

function cosine(a, b) {
  const A = tokens(a);
  const B = tokens(b);
  if (!A.size || !B.size) return 0;
  let inter = 0;
  A.forEach((t) => { if (B.has(t)) inter += 1; });
  return inter / Math.sqrt(A.size * B.size);
}

function parseMinYears(text) {
  const s = String(text || "");
  if (/\b(fresher|freshers|entry[- ]level|new grad)\b/i.test(s)) return 0;
  const re = /(\d{1,2})\s*\+?\s*(?:-|to)?\s*\d{0,2}\s*\+?\s*(?:years?|yrs?)(?:\s+of)?(?:\s+\w+){0,3}?\s+experience/gi;
  const vals = [...s.matchAll(re)].map((m) => Number(m[1])).filter((n) => n <= 25);
  return vals.length ? Math.min(...vals) : null;
}

function scoreJob(profile, job) {
  const have = new Set(canonical(profile.skills).map((s) => s.toLowerCase()));
  const need = job.skills && job.skills.length ? canonical(job.skills) : extractSkills(`${job.title}\n${job.description}`);
  const matched = need.filter((s) => have.has(s.toLowerCase()));
  const missing = need.filter((s) => !have.has(s.toLowerCase()));
  const skill = need.length ? (matched.length + 0.5) / (need.length + 1) : 0.5;

  const jobLoc = String(job.location || "").toLowerCase();
  const remote = Boolean(job.remote) || /remote|work from home|wfh/.test(jobLoc);
  let location = 0.6;
  if (remote) location = 1;
  else if (profile.work_mode === "remote") location = 0.35;
  else if (profile.location && jobLoc) {
    const city = profile.location.split(",")[0].trim().toLowerCase();
    location = jobLoc.includes(city) ? 1 : 0.2;
  }

  const roles = [...(profile.preferred_roles || []), profile.title].filter(Boolean);
  let preferences = 0.5;
  if (roles.length) {
    const J = specific(job.title);
    let best = 0;
    for (const r of roles) {
      const R = specific(r);
      let inter = 0;
      R.forEach((t) => { if (J.has(t)) inter += 1; });
      if (inter && J.size && R.size) best = Math.max(best, inter / Math.min(J.size, R.size));
    }
    preferences = 0.15 + 0.85 * best;
  }

  const needYears = job.min_years ?? parseMinYears(`${job.title}\n${job.description}`);
  const years = profile.years_experience;
  let experience = 0.8;
  if (needYears != null) {
    if (years == null) experience = 0.6;
    else if (years >= needYears) experience = years - needYears > 8 ? 0.75 : 1;
    else experience = Math.max(0, 1 + (years - needYears) / 3);
  }

  const profileText = [profile.title, ...(profile.preferred_roles || []), ...(profile.skills || [])].join(" ");
  const semantic = Math.min(1, 0.3 + cosine(profileText, `${job.title} ${need.join(" ")} ${job.description || ""}`));
  const components = { semantic, skill_overlap: skill, location, preferences, experience };
  const total = Object.entries(WEIGHTS).reduce((acc, [k, w]) => acc + w * components[k], 0);
  return {
    score: Math.round(100 * total),
    components,
    weights: WEIGHTS,
    matched_skills: matched,
    missing_skills: missing,
    reasons: [
      need.length ? `You have ${matched.length} of ${need.length} listed skills` : "Posting lists no recognisable skills",
      ...(missing.length ? [`Missing: ${missing.slice(0, 6).join(", ")}`] : []),
    ],
    engine: "fallback",
  };
}

module.exports = { scoreJob, extractSkills, canonical, parseMinYears, WEIGHTS };
