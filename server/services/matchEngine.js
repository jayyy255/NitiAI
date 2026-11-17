// HTTP client for the Python Match Engine (match_engine/). Every call has a timeout and returns
// null when the engine is down, so callers can fall back to services/matchScorer.js.
const axios = require("axios");

const BASE = (process.env.MATCH_ENGINE_URL || "http://localhost:8000").replace(/\/$/, "");
const TOKEN = process.env.MATCH_ENGINE_TOKEN || "";

const http = axios.create({
  baseURL: BASE,
  headers: TOKEN ? { "X-Internal-Token": TOKEN } : {},
});

async function call(method, url, data, timeout = 15000) {
  try {
    const res = await http.request({ method, url, data, timeout });
    return res.data;
  } catch (err) {
    const status = err.response?.status;
    console.warn(`[match-engine] ${method.toUpperCase()} ${url} failed: ${status || err.code || err.message}`);
    // 4xx (except 404/503-style availability errors) are caller mistakes: surface them
    if (status && status >= 400 && status < 500 && status !== 404) {
      const e = new Error(err.response?.data?.detail || "match engine rejected request");
      e.status = status;
      throw e;
    }
    return null;
  }
}

module.exports = {
  BASE,
  health: () => call("get", "/health", undefined, 3000),
  search: (body) => call("post", "/match/search", body, 20000),
  score: (body) => call("post", "/match/score", body, 20000),
  explain: (body) => call("post", "/match/explain", body, 45000),
  upsertJobs: (jobs) => call("post", "/jobs/upsert", jobs, 20000),
  ingest: (body) => call("post", "/jobs/ingest", body, 5000),
  jobStats: () => call("get", "/jobs/stats"),
  logEvents: (events) => call("post", "/events", events, 5000),
  agentChat: (body) => call("post", "/agent/chat", body, 90000),
  agentMemory: (userId) => call("get", `/agent/memory/${encodeURIComponent(userId)}`),
  forgetMemory: (userId, body) => call("delete", `/agent/memory/${encodeURIComponent(userId)}`, body),
  marketTrends: (days = 30) => call("get", `/market/trends?days=${Number(days) || 30}`),
  metrics: (hours = 24) => call("get", `/metrics/summary?hours=${Number(hours) || 24}`),
  trainLtr: () => call("post", "/ltr/train", undefined, 60000),
};
