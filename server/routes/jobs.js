const express = require("express");
const router = express.Router();
const axios = require("axios");
const pool = require("../config/dbConfig");
const { authenticateToken } = require("../middleware/auth");
const engine = require("../services/matchEngine");
const { scoreJob } = require("../services/matchScorer");
const { loadMatchProfile, toEngineProfile } = require("../services/profileLoader");

// Shown only when every live source fails. Scored like any other job and flagged as samples.
const sampleJobs = [
    {
        id: "sample-1",
        title: "Senior Software Engineer - AI/ML",
        company: "TechCorp",
        location: "San Francisco, CA",
        salary: "$180K - $220K",
        posted: "2 hours ago",
        description: "Looking for an experienced engineer to build AI-powered features for our core platform using Python and TensorFlow.",
        url: "https://www.linkedin.com/jobs/view/123456789"
    },
    {
        id: "sample-2",
        title: "Full-Stack Developer",
        company: "StartupXYZ",
        location: "New York, NY",
        salary: "$140K - $170K",
        posted: "5 hours ago",
        description: "Join our fast-growing team to build the next generation platform using React, Node.js, and PostgreSQL.",
        url: "https://www.linkedin.com/jobs/view/987654321"
    },
    {
        id: "sample-3",
        title: "Tech Lead - Infrastructure",
        company: "CloudScale",
        location: "Remote",
        salary: "$200K - $250K",
        posted: "1 day ago",
        description: "Lead infrastructure initiatives for our cloud-native platform. Kubernetes and AWS experience required.",
        url: "https://www.linkedin.com/jobs/view/456789123"
    },
    {
        id: "sample-4",
        title: "Data Scientist",
        company: "DataMinds",
        location: "Boston, MA",
        salary: "$160K - $190K",
        posted: "3 days ago",
        description: "Analyze large datasets to derive actionable insights and build predictive models with Python, SQL and machine learning.",
        url: "https://www.linkedin.com/jobs/view/321654987"
    }
];

const RAPIDAPI_HOST = process.env.RAPIDAPI_HOST || "linkedin-job-search-api.p.rapidapi.com";
const MIN_STORED_RESULTS = 5;

function pick(obj, keys, fallback = undefined) {
    for (const k of keys) {
        const v = k.split(".").reduce((o, part) => (o == null ? undefined : o[part]), obj);
        if (v != null && v !== "") return v;
    }
    return fallback;
}

// Normalise one RapidAPI item into the shape the client and the engine both understand.
function normaliseRapidApiJob(job) {
    let location = pick(job, ["location", "job_location", "jobLocation", "formattedLocation"]);
    if (location && typeof location === "object") location = location.city || location.name;
    if (!location && Array.isArray(job.locations_derived)) location = job.locations_derived[0];
    let salary = pick(job, ["salary_raw", "salary", "compensation", "salaryText"], "See listing");
    if (typeof salary === "object") salary = salary.text || "See listing";
    let posted = pick(job, ["date_posted", "posted_date", "postDate", "postedAt"]);
    if (!posted && job.listedAt) posted = new Date(job.listedAt).toISOString();
    const description = String(pick(job, ["description_text", "description", "job_description"], "") || "");
    const company = pick(job, ["organization", "company_name", "companyName", "company.name", "employer"]) ||
        (typeof job.company === "string" ? job.company : "Unknown Company");
    const externalId = pick(job, ["id", "job_id", "jobId"]);
    return {
        id: externalId ? String(externalId) : null,
        title: pick(job, ["title", "job_title", "jobTitle"], "Untitled Job"),
        company,
        location: location || "Remote",
        salary,
        posted: posted || "Recently",
        description,
        url: pick(job, ["url", "job_url", "linkedinJobUrl", "linkedin_url", "applyUrl", "apply_url", "link"], "#"),
        remote: job.remote_derived === true,
    };
}

async function fetchLiveJobs(title, location) {
    const key = process.env.RAPIDAPI_KEY;
    if (!key) return [];
    const headers = { "x-rapidapi-key": key, "x-rapidapi-host": RAPIDAPI_HOST };
    const extract = (data) => (Array.isArray(data) ? data : data?.data || data?.jobs || data?.results || []);
    try {
        let res = await axios.get(`https://${RAPIDAPI_HOST}/active-jb-24h`, {
            headers, timeout: 20000,
            params: { title_filter: `"${title}"`, location_filter: `"${location}"`, limit: "20", offset: "0", description_type: "text" },
        });
        let raw = extract(res.data);
        if (!raw.length) {
            res = await axios.get(`https://${RAPIDAPI_HOST}/active-jb-7d`, {
                headers, timeout: 20000,
                params: { title_filter: `"${title}"`, location_filter: `"${location.split(",").pop().trim()}"`, limit: "20", offset: "0", description_type: "text" },
            });
            raw = extract(res.data);
        }
        return raw.map(normaliseRapidApiJob);
    } catch (err) {
        console.error("RapidAPI request failed:", err.response?.status || err.message);
        return [];
    }
}

const shortDescription = (d) => (d && d.length > 220 ? `${d.substring(0, 220)}...` : d || "Open the listing for full details.");

function toClientJob(job, match, extra = {}) {
    return {
        id: job.id,
        title: job.title,
        company: job.company || "Unknown Company",
        location: job.location || (job.remote ? "Remote" : "Not specified"),
        salary: job.salary || job.salary_text || "See listing",
        posted: job.posted || (job.posted_at ? String(job.posted_at).split("T")[0] : "Recently"),
        description: shortDescription(job.description),
        url: job.url || "#",
        match: match.score,
        match_details: {
            components: match.components,
            weights: match.weights,
            matched_skills: match.matched_skills,
            missing_skills: match.missing_skills,
            reasons: match.reasons,
            engine: match.engine || "match-engine",
        },
        type: match.score >= 80 ? "recommended" : "new",
        ...extra,
    };
}

// POST /api/jobs/linkedin - jobs ranked for the signed-in user
router.post("/linkedin", authenticateToken, async (req, res) => {
    try {
        const userId = req.user.id;
        const query = req.body?.query || null;
        const title = (query || req.user.career_goal_short || "Software Engineer").replace(/-/g, " ");
        const location = req.body?.location || req.user.location || "India";

        // 1) Stored, deduplicated jobs ranked by the Match Engine (hybrid retrieval + rerank).
        const stored = await engine.search({ user_id: userId, query, k: 20 }).catch(() => null);
        if (stored?.results?.length >= MIN_STORED_RESULTS) {
            const jobs = stored.results.map((r, i) => toClientJob(r.job, r.match, { position: i + 1, source: r.job.source }));
            return res.json({ jobs, source: "match-engine", timings: stored.timings });
        }

        // 2) Live fetch. Persist to the engine (dedup + embed) and score with the engine,
        //    or with the deterministic JS scorer if the engine is down.
        let live = await fetchLiveJobs(title, location);
        let source = "live";
        if (!live.length) {
            live = sampleJobs;
            source = "sample";
        }
        live = live.map((j, i) => ({ ...j, id: j.id || `live-${i}-${Buffer.from(`${j.title}|${j.company}`).toString("base64url").slice(0, 16)}` }));

        if (source === "live") {
            engine.upsertJobs(live.map((j) => ({
                source: "rapidapi", external_id: j.id, title: j.title, company: j.company, location: j.location,
                description: j.description, url: j.url, salary_text: typeof j.salary === "string" ? j.salary : null,
                remote: j.remote || null,
            }))).catch(() => null);
        }

        const profile = await loadMatchProfile(userId);
        const engineJobs = live.map((j) => ({ id: j.id, title: j.title, company: j.company, location: j.location, description: j.description, remote: !!j.remote }));
        const scored = await engine.score({ profile: toEngineProfile(profile), jobs: engineJobs }).catch(() => null);
        const matches = scored?.scores?.length === live.length ? scored.scores : live.map((j) => scoreJob(profile, j));

        const jobs = live
            .map((j, i) => toClientJob(j, matches[i], { source }))
            .sort((a, b) => b.match - a.match)
            .map((j, i) => ({ ...j, position: i + 1 }));
        return res.json({ jobs, source, scoring: scored ? "match-engine" : "fallback" });
    } catch (error) {
        console.error("Error in /api/jobs/linkedin:", error);
        res.status(500).json({ error: "Failed to fetch jobs" });
    }
});

// POST /api/jobs/applications - Save a job application
router.post("/applications", authenticateToken, async (req, res) => {
    try {
        const { job_id, job_title, company, location, job_url, status, position, score, features } = req.body;
        const user_id = req.user.id;
        if (!job_id || !job_title) {
            return res.status(400).json({ error: "job_id and job_title are required" });
        }

        const result = await pool.query(
            `INSERT INTO job_applications (user_id, job_id, job_title, company, location, job_url, status)
             VALUES ($1, $2, $3, $4, $5, $6, COALESCE($7, 'applied'))
             ON CONFLICT (user_id, job_id)
             DO UPDATE SET
                status = COALESCE($7, job_applications.status),
                applied_at = NOW(),
                updated_at = NOW()
             RETURNING *`,
            [user_id, job_id, job_title, company || "Unknown Company", location || "Remote", job_url, status || null]
        );

        // feeds personalisation / learning-to-rank; failures must not block the save
        engine.logEvents([{
            user_id, job_id: String(job_id), event: status === "saved" ? "save" : "apply",
            position: Number.isInteger(position) ? position : null,
            score: typeof score === "number" ? score : null,
            features: features && typeof features === "object" ? features : null,
        }]).catch(() => null);

        res.status(201).json({ message: "Application saved successfully", data: result.rows[0] });
    } catch (error) {
        console.error("Error saving job application:", error);
        res.status(500).json({ error: "Failed to save application" });
    }
});

// GET /api/jobs/applications - Get user's job applications
router.get("/applications", authenticateToken, async (req, res) => {
    try {
        const result = await pool.query(
            `SELECT id, job_id, job_title, company, location, job_url, applied_at, status, notes
             FROM job_applications
             WHERE user_id = $1
             ORDER BY applied_at DESC`,
            [req.user.id]
        );
        res.json({ applications: result.rows });
    } catch (error) {
        console.error("Error fetching job applications:", error);
        res.status(500).json({ error: "Failed to fetch applications" });
    }
});

// PATCH /api/jobs/applications/:id - Update application status or notes
router.patch("/applications/:id", authenticateToken, async (req, res) => {
    try {
        const { status, notes } = req.body;
        const result = await pool.query(
            `UPDATE job_applications
             SET status = COALESCE($1, status),
                 notes = COALESCE($2, notes),
                 updated_at = NOW()
             WHERE id = $3 AND user_id = $4
             RETURNING *`,
            [status, notes, req.params.id, req.user.id]
        );
        if (result.rows.length === 0) {
            return res.status(404).json({ error: "Application not found" });
        }
        res.json({ message: "Application updated", data: result.rows[0] });
    } catch (error) {
        console.error("Error updating job application:", error);
        res.status(500).json({ error: "Failed to update application" });
    }
});

module.exports = router;
module.exports._internal = { normaliseRapidApiJob, toClientJob };
