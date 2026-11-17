// Interaction logging (impression / click / save / apply / dismiss) for personalisation and
// learning-to-rank. The user id always comes from the auth token.
const express = require("express");
const router = express.Router();
const { authenticateToken } = require("../middleware/auth");
const engine = require("../services/matchEngine");

const EVENTS = new Set(["impression", "click", "save", "apply", "dismiss"]);
const COMPONENTS = ["semantic", "skill_overlap", "location", "preferences", "experience"];

function cleanFeatures(f) {
    if (!f || typeof f !== "object") return null;
    const out = {};
    for (const k of COMPONENTS) {
        const v = Number(f[k]);
        if (!Number.isFinite(v)) return null;
        out[k] = Math.min(1, Math.max(0, v));
    }
    return out;
}

// POST /api/events { events: [{ job_id, event, position?, score?, features? }] } or a single event
router.post("/", authenticateToken, async (req, res) => {
    const raw = Array.isArray(req.body?.events) ? req.body.events : [req.body];
    const events = raw
        .filter((e) => e && e.job_id && EVENTS.has(e.event))
        .slice(0, 100)
        .map((e) => ({
            user_id: req.user.id,
            job_id: String(e.job_id).slice(0, 255),
            event: e.event,
            position: Number.isInteger(e.position) ? e.position : null,
            score: Number.isFinite(e.score) ? e.score : null,
            features: cleanFeatures(e.features),
        }));
    if (!events.length) return res.status(400).json({ error: "no valid events" });
    const out = await engine.logEvents(events).catch(() => null);
    // Logging is best-effort: never fail the user's action because analytics is down.
    res.status(202).json({ accepted: events.length, logged: out?.logged ?? 0 });
});

module.exports = router;
