// Proxies the Python Match Engine for the signed-in user. The engine trusts user_id only
// from this server (shared X-Internal-Token), never from the browser.
const express = require("express");
const router = express.Router();
const { authenticateToken } = require("../middleware/auth");
const engine = require("../services/matchEngine");
const { scoreJob } = require("../services/matchScorer");
const { loadMatchProfile, toEngineProfile } = require("../services/profileLoader");

const MODES = new Set(["bm25", "vector", "hybrid", "hybrid_rerank"]);

// POST /api/match/search { query?, k?, location?, remote_only?, mode? }
router.post("/search", authenticateToken, async (req, res) => {
    const { query, k, location, remote_only, mode } = req.body || {};
    try {
        const out = await engine.search({
            user_id: req.user.id,
            query: query || null,
            k: Math.min(Math.max(parseInt(k, 10) || 20, 1), 100),
            location: location || null,
            remote_only: !!remote_only,
            mode: MODES.has(mode) ? mode : "hybrid_rerank",
        });
        if (!out) return res.status(503).json({ error: "Match engine unavailable" });
        res.json(out);
    } catch (e) {
        res.status(e.status || 500).json({ error: e.message });
    }
});

// POST /api/match/score { jobs: [{id,title,company,location,description}] }
router.post("/score", authenticateToken, async (req, res) => {
    const jobs = Array.isArray(req.body?.jobs) ? req.body.jobs.slice(0, 50) : [];
    if (!jobs.length) return res.status(400).json({ error: "jobs array is required" });
    try {
        const profile = await loadMatchProfile(req.user.id);
        const out = await engine.score({ profile: toEngineProfile(profile), jobs: jobs.map((j, i) => ({ id: String(j.id ?? i), title: j.title || "Untitled", company: j.company, location: j.location, description: j.description || "" })) });
        if (out) return res.json({ ...out, engine: "match-engine" });
        res.json({ scores: jobs.map((j, i) => ({ job_id: String(j.id ?? i), ...scoreJob(profile, j) })), engine: "fallback" });
    } catch (e) {
        res.status(e.status || 500).json({ error: e.message });
    }
});

// POST /api/match/explain { job_id } or { job: {...} }  -> grounded, Pydantic-validated explanation
router.post("/explain", authenticateToken, async (req, res) => {
    const { job_id, job } = req.body || {};
    if (!job_id && !job) return res.status(400).json({ error: "job_id or job is required" });
    try {
        const body = { user_id: req.user.id };
        if (job) {
            body.profile = toEngineProfile(await loadMatchProfile(req.user.id));
            body.job = { id: String(job.id || "adhoc"), title: job.title || "Untitled", company: job.company, location: job.location, description: job.description || "" };
            delete body.user_id;
        } else {
            body.job_id = String(job_id);
        }
        const out = await engine.explain(body);
        if (!out) return res.status(503).json({ error: "Match engine unavailable" });
        res.json(out);
    } catch (e) {
        res.status(e.status || 500).json({ error: e.message });
    }
});

module.exports = router;
