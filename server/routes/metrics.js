// Latency / ingestion / eval dashboard data. Restricted to ADMIN_EMAILS (comma-separated).
const express = require("express");
const router = express.Router();
const { authenticateToken } = require("../middleware/auth");
const engine = require("../services/matchEngine");

function requireAdmin(req, res, next) {
    const admins = (process.env.ADMIN_EMAILS || "").split(",").map((s) => s.trim().toLowerCase()).filter(Boolean);
    if (!admins.length || !admins.includes(String(req.user?.email || "").toLowerCase())) {
        return res.status(403).json({ error: "Admin access required" });
    }
    next();
}

// GET /api/metrics/summary?hours=24
router.get("/summary", authenticateToken, requireAdmin, async (req, res) => {
    const hours = Math.min(Math.max(parseInt(req.query.hours, 10) || 24, 1), 24 * 30);
    const [summary, health] = await Promise.all([
        engine.metrics(hours).catch(() => null),
        engine.health().catch(() => null),
    ]);
    if (!summary) return res.status(503).json({ error: "Match engine unavailable", health });
    res.json({ ...summary, health });
});

// POST /api/metrics/ingest - trigger an ingestion run now
router.post("/ingest", authenticateToken, requireAdmin, async (req, res) => {
    const out = await engine.ingest({}).catch(() => null);
    if (!out) return res.status(503).json({ error: "Match engine unavailable" });
    res.status(202).json(out);
});

// POST /api/metrics/train-ltr - retrain ranking weights from interaction logs
router.post("/train-ltr", authenticateToken, requireAdmin, async (req, res) => {
    const out = await engine.trainLtr().catch(() => null);
    if (!out) return res.status(503).json({ error: "Match engine unavailable" });
    res.json(out);
});

module.exports = router;
