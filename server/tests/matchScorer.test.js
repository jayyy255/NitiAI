// node --test tests/
const test = require("node:test");
const assert = require("node:assert/strict");
const { scoreJob, extractSkills, canonical, parseMinYears, WEIGHTS } = require("../services/matchScorer");

const job = {
  title: "Backend Engineer",
  location: "Pune, India",
  description: "Node.js, PostgreSQL and Redis. Requires 2+ years of experience.",
};

test("score is deterministic (no Math.random) and equals the weighted sum", () => {
  const p = { skills: ["Node.js", "PostgreSQL"], years_experience: 2, location: "Pune", preferred_roles: ["Backend Engineer"] };
  const a = scoreJob(p, job);
  const b = scoreJob(p, job);
  assert.deepEqual(a, b);
  const expected = Object.entries(WEIGHTS).reduce((acc, [k, w]) => acc + w * a.components[k], 0);
  assert.equal(a.score, Math.round(100 * expected));
  assert.deepEqual(a.matched_skills.sort(), ["Node.js", "PostgreSQL"]);
  assert.deepEqual(a.missing_skills, ["Redis"]);
});

test("more matching skills and enough experience score higher", () => {
  const weak = scoreJob({ skills: [], years_experience: 0 }, job).score;
  const strong = scoreJob({ skills: ["Node.js", "PostgreSQL", "Redis"], years_experience: 3 }, job).score;
  assert.ok(strong > weak, `${strong} > ${weak}`);
});

test("remote preference penalises on-site roles", () => {
  const p = { work_mode: "remote" };
  assert.equal(scoreJob(p, { ...job, location: "Remote" }).components.location, 1);
  assert.ok(scoreJob(p, job).components.location < 0.5);
});

test("skill extraction and aliases", () => {
  assert.deepEqual(extractSkills("React Native app with C++ core").sort(), ["C++", "React Native"]);
  assert.deepEqual(canonical(["nodejs", "Postgres", "k8s"]), ["Node.js", "PostgreSQL", "Kubernetes"]);
});

test("experience parsing", () => {
  assert.equal(parseMinYears("3+ years of experience"), 3);
  assert.equal(parseMinYears("Freshers welcome"), 0);
  assert.equal(parseMinYears("Great team"), null);
});

test("jobs route normalises RapidAPI payload variants", () => {
  process.env.GEMINI_API_KEY = process.env.GEMINI_API_KEY || "test";
  const { normaliseRapidApiJob } = require("../routes/jobs")._internal;
  const j = normaliseRapidApiJob({ id: 7, title: "SDE", organization: "Acme", locations_derived: ["Pune, India"], description_text: "Java" });
  assert.equal(j.id, "7");
  assert.equal(j.company, "Acme");
  assert.equal(j.location, "Pune, India");
  const k = normaliseRapidApiJob({ job_title: "QA", company: { name: "Beta" }, location: { city: "Delhi" } });
  assert.equal(k.company, "Beta");
  assert.equal(k.location, "Delhi");
  assert.equal(k.id, null);
});
