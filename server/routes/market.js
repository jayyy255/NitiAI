// Market trends computed from stored RapidAPI + Adzuna postings (match engine jobs table).
const express = require("express");
const router = express.Router();
const { authenticateToken } = require("../middleware/auth");
const engine = require("../services/matchEngine");

// GET /api/market/trends?days=30
router.get("/trends", authenticateToken, async (req, res) => {
    const days = Math.min(Math.max(parseInt(req.query.days, 10) || 30, 1), 180);
    const out = await engine.marketTrends(days).catch(() => null);
    if (!out) return res.status(503).json({ error: "Market data unavailable" });
    res.json(out);
});

module.exports = router;
