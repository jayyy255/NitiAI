const express = require("express");
const router = express.Router();
const { gradeSubmission } = require("../services/codeGrader");

const EXECUTION_API_URL = process.env.EXECUTION_API_URL || "http://localhost:2000/api/v2/execute";

// Last resort when the Piston execution service is unreachable: ask the LLM to *estimate* results.
// Always labelled engine: "ai-estimate" so the UI can say the code was not actually run.
async function aiEstimate({ code, language, testCases, problemTitle }) {
    const sarvamService = require("../services/sarvamService");
    const prompt = `You are reviewing ${language} code for the problem "${problemTitle || "Coding Challenge"}".
Mentally trace the code for each test case and predict its return value.

USER CODE:
\`\`\`${language}
${code}
\`\`\`

TEST CASES:
${JSON.stringify(testCases || [], null, 2)}

Return ONLY JSON: {"results":[{"input":"...","expected":"...","actual":"<predicted output>","passed":true|false}]}`;
    const raw = await sarvamService.generateCompletion(prompt, "You are a meticulous code reviewer. Respond with JSON only.");
    let json = raw.replace(/```json\n?|\n?```/g, "").trim();
    json = json.substring(json.indexOf("{"), json.lastIndexOf("}") + 1);
    const parsed = JSON.parse(json);
    const results = (parsed.results || []).map((r, i) => ({
        passed: r.passed === true,
        input: r.input ?? testCases[i]?.input,
        expected: r.expected ?? testCases[i]?.output,
        actual: String(r.actual ?? ""),
        stdout: "",
    }));
    return {
        success: results.length > 0 && results.every((r) => r.passed),
        engine: "ai-estimate",
        results,
        output: "Code execution service unavailable: these results are an AI estimate, not a real run.",
        compilerOutput: "",
    };
}

// POST /api/execute { code, language: python|javascript|cpp, testCases?: [{input, output}], problemTitle? }
router.post("/", async (req, res) => {
    const { code, language, testCases, problemTitle } = req.body || {};
    if (!code || !language) {
        return res.status(400).json({ error: "Code and language are required" });
    }

    try {
        const result = await gradeSubmission({ code, language, testCases, executionUrl: EXECUTION_API_URL });
        return res.json(result);
    } catch (err) {
        console.warn("Code execution service unreachable:", err.message);
    }

    try {
        return res.json(await aiEstimate({ code, language, testCases: testCases || [], problemTitle }));
    } catch (llmErr) {
        console.error("AI estimate fallback failed:", llmErr.message);
        return res.status(503).json({
            success: false,
            engine: "unavailable",
            error: "Code execution service is unavailable. Please try again later.",
            results: (testCases || []).map((tc) => ({ passed: false, input: tc.input, expected: tc.output, actual: "", stdout: "", error: "Not run" })),
        });
    }
});

module.exports = router;
