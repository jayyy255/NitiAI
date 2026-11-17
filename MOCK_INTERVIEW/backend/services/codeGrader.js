// Copy of server/services/codeGrader.js; keep the two files identical.
// Server-side code grader: runs a candidate's solution against test cases on Piston and compares
// actual vs expected output. Used by server/routes/execute.js and (as a copy) by
// MOCK_INTERVIEW/backend/services/codeGrader.js; keep the two files identical.
//
// Test cases look like { input: "nums = [2,7,11,15], target = 9", output: "[0,1]" }.
// - Python / JavaScript: we find the entry function (solution(), a Solution class method, or the
//   first top-level function), append a driver that calls it once per test case with the parsed
//   arguments, and print each return value as JSON between markers. One Piston call per submission.
// - C++: generic drivers are impossible without types, so a C++ submission must contain main()
//   reading stdin; each test case's input is passed as stdin and stdout is compared.
//
// Result shape (both backends): { success, engine, output, compilerOutput, results: [...], error? }
const axios = require("axios");

const RUNTIMES = {
    python: { language: "python", version: "3.10.0" },
    javascript: { language: "javascript", version: "18.15.0" },
    cpp: { language: "c++", version: "10.2.0" },
};
const MARK_START = "<<<NITI_CASE_START>>>";
const MARK_RVAL = "<<<NITI_RVAL>>>";
const MARK_ERR = "<<<NITI_ERROR>>>";

// ---------------------------------------------------------------- input parsing

// Split "a = 1, b = [1,2], c = 'x,y'" at top-level commas.
function splitArgs(input) {
    const args = [];
    let cur = "", depth = 0, quote = null;
    const s = String(input ?? "").trim();
    for (let i = 0; i < s.length; i++) {
        const ch = s[i];
        if (quote) {
            cur += ch;
            if (ch === quote && s[i - 1] !== "\\") quote = null;
            continue;
        }
        if (ch === '"' || ch === "'") { quote = ch; cur += ch; continue; }
        if ("[{(".includes(ch)) depth++;
        if ("]})".includes(ch)) depth--;
        if (ch === "," && depth === 0) { args.push(cur.trim()); cur = ""; continue; }
        cur += ch;
    }
    if (cur.trim()) args.push(cur.trim());
    // drop "name =" prefixes (but not "==" comparisons inside values)
    return args.map((a) => a.replace(/^[A-Za-z_]\w*\s*=(?!=)\s*/, ""));
}

// Make a single argument a valid literal in both Python and JavaScript.
function toLiteral(arg) {
    const v = arg.trim();
    if (!v) return "None";
    if (/^(true|false|null|None|True|False)$/.test(v)) return v;
    if (/^-?\d+(\.\d+)?(e-?\d+)?$/i.test(v)) return v;
    if (/^["'[{(]/.test(v)) return v;
    return JSON.stringify(v); // bare word -> string
}

// ---------------------------------------------------------------- entry point detection

function pythonEntry(code) {
    if (/^def\s+solution\s*\(/m.test(code)) return "solution";
    const cls = code.match(/^class\s+Solution\b[^:]*:\s*\n((?:[ \t]+.*\n?|\s*\n)+)/m);
    if (cls) {
        const m = cls[1].match(/^\s+def\s+([A-Za-z_]\w*)\s*\(\s*self/m);
        if (m && !m[1].startsWith("__")) return `Solution().${m[1]}`;
    }
    const top = code.match(/^def\s+([A-Za-z_]\w*)\s*\(/m);
    return top ? top[1] : null;
}

function jsEntry(code) {
    if (/\bfunction\s+solution\s*\(/.test(code) || /\b(const|let|var)\s+solution\s*=/.test(code)) return "solution";
    if (/\bclass\s+Solution\b/.test(code)) {
        const m = code.match(/class\s+Solution\b[^{]*\{[\s\S]*?\n?\s*(?!constructor\b)([A-Za-z_$][\w$]*)\s*\([^)]*\)\s*\{/);
        if (m) return `new Solution().${m[1]}`;
    }
    const fn = code.match(/\bfunction\s+([A-Za-z_$][\w$]*)\s*\(/) ||
        code.match(/\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:function\b|\([^)]*\)\s*=>|[A-Za-z_$][\w$]*\s*=>)/);
    return fn ? fn[1] : null;
}

// ---------------------------------------------------------------- drivers

function pythonDriver(code, entry, testCases) {
    const calls = testCases.map((tc) => {
        const args = splitArgs(tc.input).map(toLiteral).join(", ");
        return [
            `print(${JSON.stringify(MARK_START)})`,
            "try:",
            `    __r = ${entry}(${args})`,
            `    print(${JSON.stringify(MARK_RVAL)})`,
            "    print(__json.dumps(__norm(__r)))",
            "except Exception as __e:",
            `    print(${JSON.stringify(MARK_ERR)})`,
            "    print(type(__e).__name__ + ': ' + str(__e))",
        ].join("\n");
    }).join("\n");
    // One-line prelude so user line numbers are off by exactly PY_PRELUDE_LINES (see fixLineNumbers).
    return `null = None; true = True; false = False; import json as __json; from typing import *\n${code}\n\n` +
        `def __norm(v):\n    if isinstance(v, (set, frozenset)):\n        return sorted(__norm(x) for x in v)\n` +
        `    if isinstance(v, tuple):\n        return [__norm(x) for x in v]\n    if isinstance(v, list):\n        return [__norm(x) for x in v]\n` +
        `    if isinstance(v, dict):\n        return {str(k): __norm(x) for k, x in v.items()}\n    return v\n\n${calls}\n`;
}

function jsDriver(code, entry, testCases) {
    const calls = testCases.map((tc) => {
        const args = splitArgs(tc.input).map(toLiteral).map((a) => a.replace(/^None$/, "null").replace(/^True$/, "true").replace(/^False$/, "false")).join(", ");
        return `console.log(${JSON.stringify(MARK_START)});\ntry { const __r = ${entry}(${args}); console.log(${JSON.stringify(MARK_RVAL)}); console.log(JSON.stringify(__r === undefined ? null : __r)); } catch (__e) { console.log(${JSON.stringify(MARK_ERR)}); console.log(String(__e && __e.message || __e)); }`;
    }).join("\n");
    return `${code}\n\n${calls}\n`;
}

// ---------------------------------------------------------------- comparison

function parseValue(s) {
    const t = String(s ?? "").trim();
    const attempts = [t, t.replace(/'/g, '"').replace(/\bNone\b/g, "null").replace(/\bTrue\b/g, "true").replace(/\bFalse\b/g, "false")];
    for (const a of attempts) {
        try { return { ok: true, v: JSON.parse(a) }; } catch { /* try next */ }
    }
    return { ok: false, v: t };
}

function deepEqual(a, b) {
    if (typeof a === "number" && typeof b === "number") return Math.abs(a - b) <= 1e-6 * Math.max(1, Math.abs(b));
    if (Array.isArray(a) && Array.isArray(b)) return a.length === b.length && a.every((x, i) => deepEqual(x, b[i]));
    if (a && b && typeof a === "object" && typeof b === "object") {
        const ka = Object.keys(a).sort(), kb = Object.keys(b).sort();
        return deepEqual(ka, kb) && ka.every((k) => deepEqual(a[k], b[k]));
    }
    return a === b;
}

function outputsMatch(actual, expected) {
    const A = parseValue(actual), E = parseValue(expected);
    if (A.ok && E.ok) return deepEqual(A.v, E.v);
    const norm = (x) => String(x).replace(/\s+/g, "").replace(/^"(.*)"$/, "$1").toLowerCase();
    return norm(A.ok ? JSON.stringify(A.v) : A.v) === norm(E.ok ? JSON.stringify(E.v) : E.v);
}

// ---------------------------------------------------------------- running

class ExecutionRequestError extends Error {}

// Network failures / 5xx throw (the service is unavailable -> caller may fall back).
// A 4xx means Piston rejected this submission (e.g. runtime not installed): surface it as a result.
async function piston(url, runtime, content, stdin = "") {
    try {
        const { data } = await axios.post(url, {
            language: runtime.language, version: runtime.version, files: [{ content }], stdin,
            compile_timeout: 10000, run_timeout: 3000, // Piston's default maximum run_timeout is 3000 ms
        }, { timeout: 20000, headers: { "Content-Type": "application/json" } });
        return data;
    } catch (err) {
        const status = err.response && err.response.status;
        if (status && status < 500) {
            throw new ExecutionRequestError((err.response.data && err.response.data.message) || `Execution request rejected (${status})`);
        }
        throw err;
    }
}

function compileFailure(data) {
    const c = data && data.compile;
    return c && c.code !== 0 ? (c.stderr || c.output || "Compilation failed") : null;
}

const PY_PRELUDE_LINES = 1;

// Tracebacks point at Piston's temp file and count our prelude; map them back to the user's code.
function fixLineNumbers(text, language) {
    if (!text) return text;
    let t = String(text).replace(/\/piston\/jobs\/[\w-]+\/file0\.code/g, "solution");
    if (language === "python") {
        t = t.replace(/(File "solution", line )(\d+)/g, (_, p, n) => p + Math.max(1, Number(n) - PY_PRELUDE_LINES));
    }
    return t;
}

function parseMarkedOutput(stdout, testCases) {
    const parts = String(stdout || "").split(MARK_START);
    const preamble = parts[0].trim();
    return testCases.map((tc, i) => {
        const chunk = parts[i + 1];
        if (chunk === undefined) {
            return { passed: false, input: tc.input, expected: tc.output, actual: "", stdout: "", error: "Not reached (program stopped earlier)" };
        }
        if (chunk.includes(MARK_ERR)) {
            const [out, err] = chunk.split(MARK_ERR);
            return { passed: false, input: tc.input, expected: tc.output, actual: "", stdout: out.trim(), error: err.trim() };
        }
        const [out, rval = ""] = chunk.split(MARK_RVAL);
        const actual = rval.trim();
        return { passed: outputsMatch(actual, tc.output), input: tc.input, expected: tc.output, actual, stdout: out.trim() };
    }).map((r, i) => (i === 0 && preamble ? { ...r, stdout: [preamble, r.stdout].filter(Boolean).join("\n") } : r));
}

/**
 * Grade a submission. Throws only when the execution service itself is unreachable
 * (callers decide whether to fall back to an AI estimate).
 */
async function gradeSubmission(args) {
    try {
        return await gradeInner(args);
    } catch (err) {
        if (err instanceof ExecutionRequestError) {
            const cases = (args.testCases || []).filter(Boolean);
            return { success: false, engine: "piston", error: err.message, output: err.message, compilerOutput: "",
                results: cases.map((tc) => ({ passed: false, input: tc.input, expected: tc.output, actual: "", stdout: "", error: err.message })) };
        }
        throw err;
    }
}

async function gradeInner({ code, language, testCases = [], executionUrl }) {
    const runtime = RUNTIMES[language];
    if (!runtime) return { success: false, engine: "piston", error: `Unsupported language: ${language}`, results: [] };
    const cases = (testCases || []).filter((tc) => tc && tc.input !== undefined && tc.output !== undefined);

    // Plain run (no test cases): just execute and show output.
    if (!cases.length) {
        const data = await piston(executionUrl, runtime, code);
        const cerr = compileFailure(data);
        const run = data.run || {};
        return {
            success: !cerr && run.code === 0, engine: "piston", results: [],
            output: fixLineNumbers(cerr || run.output || "", language), compilerOutput: fixLineNumbers(cerr || "", language),
            error: cerr ? "Compilation failed" : run.code !== 0 ? fixLineNumbers((run.stderr || "Runtime error").trim(), language) : undefined,
        };
    }

    if (language === "cpp") {
        if (!/\bint\s+main\s*\(/.test(code)) {
            return {
                success: false, engine: "piston", compilerOutput: "",
                error: "C++ submissions are graded by running your program: add an int main() that reads the input from stdin and prints the answer.",
                results: cases.map((tc) => ({ passed: false, input: tc.input, expected: tc.output, actual: "", stdout: "", error: "No main() function" })),
            };
        }
        const results = [];
        let compilerOutput = "";
        for (const tc of cases) {
            const data = await piston(executionUrl, runtime, code, String(tc.input));
            const cerr = compileFailure(data);
            if (cerr) {
                compilerOutput = cerr;
                return { success: false, engine: "piston", compilerOutput, error: "Compilation failed", output: cerr,
                    results: cases.map((c) => ({ passed: false, input: c.input, expected: c.output, actual: "", stdout: "", error: "Compilation failed" })) };
            }
            const run = data.run || {};
            const actual = (run.stdout || "").trim();
            results.push({ passed: run.code === 0 && outputsMatch(actual, tc.output), input: tc.input, expected: tc.output,
                actual, stdout: actual, error: run.code !== 0 ? (run.stderr || `Exited with code ${run.code}`).trim() : undefined });
        }
        return { success: results.every((r) => r.passed), engine: "piston", compilerOutput, results,
            output: results.map((r, i) => `Test ${i + 1}: ${r.passed ? "passed" : "failed"}`).join("\n") };
    }

    const entry = language === "python" ? pythonEntry(code) : jsEntry(code);
    if (!entry) {
        return { success: false, engine: "piston", error: "Could not find a function to test. Define your solution as a function (e.g. solution(...)).",
            results: cases.map((tc) => ({ passed: false, input: tc.input, expected: tc.output, actual: "", stdout: "", error: "No function found" })) };
    }
    const program = language === "python" ? pythonDriver(code, entry, cases) : jsDriver(code, entry, cases);
    const data = await piston(executionUrl, runtime, program);
    const run = data.run || {};
    let results = parseMarkedOutput(run.stdout, cases);
    let error;
    if (run.signal === "SIGKILL" || /timed? ?out/i.test(run.stderr || "")) error = "Time limit exceeded";
    else if (run.code !== 0 && results.every((r) => r.error)) error = (run.stderr || "Runtime error").trim();
    if (error) results = results.map((r) => (r.error && !r.actual ? { ...r, error: r.error === "Not reached (program stopped earlier)" ? error : r.error } : r));
    return {
        success: !error && results.every((r) => r.passed), engine: "piston", entry,
        results: results.map((r) => (r.error ? { ...r, error: fixLineNumbers(r.error, language) } : r)),
        compilerOutput: run.code !== 0 && run.stderr && !results.some((r) => r.actual) ? fixLineNumbers(run.stderr.trim(), language) : "",
        output: results.map((r, i) => `Test ${i + 1}: ${r.passed ? "passed" : "failed"}`).join("\n"),
        error: fixLineNumbers(error, language),
    };
}

module.exports = { gradeSubmission, splitArgs, toLiteral, pythonEntry, jsEntry, outputsMatch, RUNTIMES };
