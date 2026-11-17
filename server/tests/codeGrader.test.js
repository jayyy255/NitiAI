// Unit tests run offline. Set PISTON_URL to also run the integration tests against a real Piston:
//   PISTON_URL=http://localhost:2000/api/v2/execute node --test tests/codeGrader.test.js
const test = require("node:test");
const assert = require("node:assert/strict");
const { gradeSubmission, splitArgs, toLiteral, pythonEntry, jsEntry, outputsMatch } = require("../services/codeGrader");

test("splitArgs strips names and respects nesting/quotes", () => {
  assert.deepEqual(splitArgs("nums = [2,7,11,15], target = 9"), ["[2,7,11,15]", "9"]);
  assert.deepEqual(splitArgs("3, 1000, [0, 100, 200]"), ["3", "1000", "[0, 100, 200]"]);
  assert.deepEqual(splitArgs(`s = "a,b", m = {"x": [1,2]}`), ['"a,b"', '{"x": [1,2]}']);
  assert.deepEqual(splitArgs("2, ['put(1,1)', 'get(1)']"), ["2", "['put(1,1)', 'get(1)']"]);
});

test("toLiteral quotes bare words only", () => {
  assert.equal(toLiteral("abcabcbb"), '"abcabcbb"');
  assert.equal(toLiteral("42"), "42");
  assert.equal(toLiteral("[1,2]"), "[1,2]");
  assert.equal(toLiteral("true"), "true");
});

test("entry point detection", () => {
  assert.equal(pythonEntry("def solution(a):\n    return a"), "solution");
  assert.equal(pythonEntry("class Solution:\n    def twoSum(self, nums, target):\n        pass"), "Solution().twoSum");
  assert.equal(pythonEntry("def helper(x):\n    pass\ndef lengthOfLongestSubstring(s):\n    pass"), "helper");
  assert.equal(jsEntry("function lengthOfLongestSubstring(s) {}"), "lengthOfLongestSubstring");
  assert.equal(jsEntry("const twoSum = (nums, t) => []"), "twoSum");
  assert.equal(jsEntry("class Solution {\n  maxProfit(prices) { return 0 }\n}"), "new Solution().maxProfit");
  assert.equal(jsEntry("// nothing here"), null);
});

test("output comparison is type- and format-tolerant", () => {
  assert.ok(outputsMatch("[0, 1]", "[0,1]"));
  assert.ok(outputsMatch("true", "True"));
  assert.ok(outputsMatch("null", "None"));
  assert.ok(outputsMatch('"abc"', "abc"));
  assert.ok(outputsMatch("[true, false]", "[true,false]"));
  assert.ok(outputsMatch("0.30000000000000004", "0.3"));
  assert.ok(!outputsMatch("[1, 0]", "[0,1]"));
  assert.ok(!outputsMatch("3", "4"));
});

test("unsupported language and missing function fail clearly without calling Piston", async () => {
  const a = await gradeSubmission({ code: "x", language: "ruby", testCases: [], executionUrl: "http://127.0.0.1:1" });
  assert.equal(a.success, false);
  const b = await gradeSubmission({ code: "# empty", language: "python", testCases: [{ input: "1", output: "1" }], executionUrl: "http://127.0.0.1:1" });
  assert.equal(b.success, false);
  assert.match(b.error, /function/);
  const c = await gradeSubmission({ code: "class Solution{};", language: "cpp", testCases: [{ input: "1", output: "1" }], executionUrl: "http://127.0.0.1:1" });
  assert.match(c.error, /main\(\)/);
});

test("unreachable execution service throws (caller falls back)", async () => {
  await assert.rejects(gradeSubmission({ code: "def solution(a): return a", language: "python", testCases: [{ input: "1", output: "1" }], executionUrl: "http://127.0.0.1:1/x" }));
});

const URL = process.env.PISTON_URL;
const live = URL ? test : test.skip;
const twoSumCases = [
  { input: "nums = [2,7,11,15], target = 9", output: "[0,1]" },
  { input: "nums = [3,2,4], target = 6", output: "[1,2]" },
  { input: "nums = [3,3], target = 6", output: "[0,1]" },
];

live("python: correct solution passes every case", async () => {
  const code = "def twoSum(nums, target):\n    seen = {}\n    for i, n in enumerate(nums):\n        if target - n in seen:\n            return [seen[target - n], i]\n        seen[n] = i";
  const r = await gradeSubmission({ code, language: "python", testCases: twoSumCases, executionUrl: URL });
  assert.equal(r.success, true, JSON.stringify(r));
  assert.equal(r.results.filter((x) => x.passed).length, 3);
});

live("python: wrong answer fails with the actual value shown", async () => {
  const r = await gradeSubmission({ code: "def twoSum(nums, target):\n    return [0, 0]", language: "python", testCases: twoSumCases, executionUrl: URL });
  assert.equal(r.success, false);
  assert.equal(r.results[0].actual, "[0, 0]");
  assert.ok(r.results.every((x) => !x.passed));
});

live("python: Solution class, print debugging and one crashing case", async () => {
  const code = "class Solution:\n    def lengthOfLongestSubstring(self, s):\n        print('debug', s)\n        if s == 'boom': raise ValueError('bad')\n        best = l = 0\n        seen = {}\n        for r, c in enumerate(s):\n            if c in seen and seen[c] >= l: l = seen[c] + 1\n            seen[c] = r\n            best = max(best, r - l + 1)\n        return best";
  const cases = [{ input: 's = "abcabcbb"', output: "3" }, { input: "s = boom", output: "0" }, { input: 's = "pwwkew"', output: "3" }];
  const r = await gradeSubmission({ code, language: "python", testCases: cases, executionUrl: URL });
  assert.deepEqual(r.results.map((x) => x.passed), [true, false, true]);
  assert.match(r.results[1].error, /ValueError: bad/);
  assert.match(r.results[0].stdout, /debug abcabcbb/);
});

live("javascript: correct, wrong and infinite loop", async () => {
  const ok = await gradeSubmission({ code: "function solution(a, b) { return a + b; }", language: "javascript",
    testCases: [{ input: "2, 3", output: "5" }, { input: "a = -1, b = 1", output: "0" }], executionUrl: URL });
  assert.equal(ok.success, true, JSON.stringify(ok));
  const bad = await gradeSubmission({ code: "const solution = (a, b) => a - b;", language: "javascript",
    testCases: [{ input: "2, 3", output: "5" }], executionUrl: URL });
  assert.equal(bad.success, false);
  const loop = await gradeSubmission({ code: "function solution() { while (true) {} }", language: "javascript",
    testCases: [{ input: "1", output: "1" }], executionUrl: URL });
  assert.equal(loop.success, false);
});

live("cpp: main() reading stdin is graded per case; compile errors reported", async () => {
  const code = "#include <iostream>\nint main(){ long a,b; std::cin>>a>>b; std::cout<<a+b; }";
  const r = await gradeSubmission({ code, language: "cpp", testCases: [{ input: "2 3", output: "5" }, { input: "10 -4", output: "6" }, { input: "1 1", output: "3" }], executionUrl: URL });
  assert.deepEqual(r.results.map((x) => x.passed), [true, true, false]);
  const ce = await gradeSubmission({ code: "int main( { }", language: "cpp", testCases: [{ input: "1", output: "1" }], executionUrl: URL });
  assert.equal(ce.error, "Compilation failed");
  assert.ok(ce.compilerOutput.length > 0);
});

live("plain run without test cases returns program output", async () => {
  const r = await gradeSubmission({ code: "print('hello')", language: "python", testCases: [], executionUrl: URL });
  assert.equal(r.success, true);
  assert.equal(r.output.trim(), "hello");
});

live("python errors report the user's own line numbers", async () => {
  const r = await gradeSubmission({ code: "def solution(a)\n    return a", language: "python", testCases: [{ input: "1", output: "1" }], executionUrl: URL });
  assert.match(r.results[0].error, /File "solution", line 1\b/);
  const rt = await gradeSubmission({ code: "def solution(a):\n    x = 1\n    return a / 0", language: "python", testCases: [{ input: "1", output: "1" }], executionUrl: URL });
  assert.match(rt.results[0].error, /ZeroDivisionError/);
});
