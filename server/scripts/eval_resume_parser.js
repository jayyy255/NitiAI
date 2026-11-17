// Runs the production resume parser (routes/resumeParser.js -> parseResumeBasic) over the
// eval fixtures and writes raw outputs as JSON. Used by match_engine/eval/run_eval.py.
// Usage: node scripts/eval_resume_parser.js <resume_dir> <out.json>
require("dotenv").config({ path: require("path").join(__dirname, "..", ".env") });
const fs = require("fs");
const path = require("path");

(async () => {
  const [dir, out] = process.argv.slice(2);
  if (!dir || !out) {
    console.error("usage: node scripts/eval_resume_parser.js <resume_dir> <out.json>");
    process.exit(2);
  }
  const service = require("../routes/resumeParser");
  const results = {};
  for (const f of fs.readdirSync(dir).filter((x) => x.endsWith(".txt")).sort()) {
    const id = path.basename(f, ".txt");
    try {
      results[id] = await service.parseResumeBasic(fs.readFileSync(path.join(dir, f), "utf8"));
      console.error(`parsed ${id}`);
    } catch (e) {
      results[id] = { error: e.message };
      console.error(`failed ${id}: ${e.message}`);
    }
  }
  fs.writeFileSync(out, JSON.stringify(results, null, 2));
})();
