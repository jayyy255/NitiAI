// Tool-using career coach (search_jobs, get_profile, skill_gap, explain_match, save_job, remember)
// with persistent memory. Runs in the Match Engine; this router adds auth.
const express = require("express");
const router = express.Router();
const { authenticateToken } = require("../middleware/auth");
const engine = require("../services/matchEngine");

// POST /api/agent/chat { message }
router.post("/chat", authenticateToken, async (req, res) => {
    const message = String(req.body?.message || "").trim();
    if (!message) return res.status(400).json({ error: "message is required" });
    if (message.length > 4000) return res.status(400).json({ error: "message too long" });
    try {
        const out = await engine.agentChat({ user_id: req.user.id, message });
        if (!out) return res.status(503).json({ error: "Career agent unavailable" });
        res.json(out);
    } catch (e) {
        res.status(e.status || 500).json({ error: e.message });
    }
});

// GET /api/agent/memory - what the coach remembers about me
router.get("/memory", authenticateToken, async (req, res) => {
    try {
        const out = await engine.agentMemory(req.user.id);
        if (!out) return res.status(503).json({ error: "Career agent unavailable" });
        res.json(out);
    } catch (e) {
        res.status(e.status || 500).json({ error: e.message });
    }
});

// DELETE /api/agent/memory { kind, key } - forget one fact
router.delete("/memory", authenticateToken, async (req, res) => {
    const { kind, key } = req.body || {};
    if (!kind || !key) return res.status(400).json({ error: "kind and key are required" });
    try {
        const out = await engine.forgetMemory(req.user.id, { kind, key });
        if (!out) return res.status(503).json({ error: "Career agent unavailable" });
        res.json(out);
    } catch (e) {
        res.status(e.status || 500).json({ error: e.message });
    }
});

module.exports = router;
