# NitiAI - Comprehensive AI Career Guidance Platform 🚀

NitiAI is an all-in-one intelligent career acceleration platform. It helps users analyze resumes, gain market insights, generate professional portfolios, and practice with a state-of-the-art Mock Interview Arena.

## 🔗 Deployment
The live application is accessible at: **[https://nitiai.vercel.app](https://nitiai.vercel.app)**

---

## 🌟 Key Features

### 1. NitiAI Core (Career Guidance)
*   **📄 AI Resume Parsing**: Extracts skills, experience, and education from PDFs using Google Gemini.
*   **🎭 Career Persona**: Deep analysis of your professional profile to highlight unique value propositions.
*   **🤖 AI Career Coach**: Tool-using agent (`search_jobs`, `get_profile`, `skill_gap`, `explain_match`, `save_job`, `remember`) with persistent memory across sessions; falls back to plain Gemini chat if the engine is down.
*   **💼 Job Matching & Tracker**: Jobs ranked by an explainable match score (hybrid retrieval + cross-encoder rerank), with a per-job breakdown of matched/missing skills; save and track applications.
*   **📈 Market Trends**: Trending roles, top and rising skills, locations and salaries aggregated from stored RapidAPI (LinkedIn) + Adzuna postings.
*   **🧭 Career Recommendations**: Career paths scored by skill coverage, experience and preferences, with live posting demand.
*   **🌐 Portfolio Builder**: Generates stunning, downloadable personal portfolios.

### 2. Mock Interview Arena
*   **💻 Coding Environment**: Real-time collaborative editor with live code execution.
*   **🎙️ Voice Interactor**: AI-powered voice interviews for a hands-free, realistic experience (powered by Vapi).
*   **📊 Performance Reports**: Detailed post-interview feedback on code quality, logic, and communication.
*   **🔍 Resource Discovery**: Targeted YouTube resources based on your interview performance.

---

## 🎯 Match Engine (`/match_engine`)

A Python/FastAPI service that runs next to the Express API and replaces the old random job score
(`Math.random()` 70–99) and the hardcoded career-recommendation scores.

```
Browser ──► Express (/server) ──X-Internal-Token──► Match Engine (FastAPI)
                │                                     │
                └────────────── Postgres + pgvector ◄─┘
                                 users, resume_info, skills, …   (existing)
                                 jobs, ingest_runs, job_interactions,
                                 coach_memory, coach_messages, request_logs (new)
```

| Piece | What it does | Code |
|---|---|---|
| Ingestion | Scheduled pulls from RapidAPI (LinkedIn) and Adzuna → normalise → dedupe (title + company + city key; richest description wins) → upsert → expire stale postings → embed only new/changed jobs | `app/ingest/` |
| Hybrid retrieval | Postgres full-text (`ts_rank_cd`) + pgvector cosine (bge-small, HNSW), fused with Reciprocal Rank Fusion | `app/retrieval.py` |
| Reranking + score | Cross-encoder (ms-marco MiniLM, ONNX) blended with skill overlap, location, preferences and experience: `score = 100 · Σ wᵢ·cᵢ`, every component returned | `app/rerank.py`, `app/scoring.py` |
| Grounded explanations | Gemini JSON output validated with Pydantic; every cited strength must quote text that exists in both the profile and the posting, otherwise it is dropped. Deterministic template when no LLM | `app/explain.py` |
| Career agent | Gemini function calling over the tools above, memory in `coach_memory` / `coach_messages` | `app/agent.py`, `app/memory.py` |
| Personalisation | Impression/click/save/apply logged with the score components shown; logistic-regression learning-to-rank writes new weights only if held-out AUC improves | `app/ltr.py` |
| Observability | Per-route latency (p50/p95/p99) into `request_logs`, structured logs, `/admin/metrics` page in the client | `app/telemetry.py` |

If the engine is unreachable, Express falls back to a deterministic JS port of the same formula
(`server/services/matchScorer.js`) — scores are never random.

### New API routes (Express)
`POST /api/match/search` · `POST /api/match/score` · `POST /api/match/explain` · `POST /api/agent/chat` ·
`GET|DELETE /api/agent/memory` · `POST /api/events` · `GET /api/market/trends` ·
`GET /api/metrics/summary`, `POST /api/metrics/ingest`, `POST /api/metrics/train-ltr` (admin, `ADMIN_EMAILS`).
`POST /api/jobs/linkedin` now serves stored jobs ranked by the engine, else live-fetches, persists and scores them.

### Running it
```bash
cd match_engine
python -m venv .venv && .venv/Scripts/pip install -r requirements-dev.txt   # or .venv/bin/pip
uvicorn app.main:app --port 8000
```
Env (reads `match_engine/.env`, then `server/.env`): `DATABASE_URL`, `MATCH_ENGINE_TOKEN` (same value in `server/.env`),
`GEMINI_API_KEY`, `RAPIDAPI_KEY`, `ADZUNA_APP_ID`, `ADZUNA_APP_KEY`, `INGEST_ENABLED=true`, `INGEST_INTERVAL_HOURS`.
Express needs `MATCH_ENGINE_URL` (default `http://localhost:8000`) and `MATCH_ENGINE_TOKEN`. The database needs the `vector` extension (Neon supports it); the engine runs its migration on start.

With Docker: `docker compose up --build` starts the client, server and match engine (port 8331; models baked into the image, ingestion enabled).
The engine reads `server/.env`; `server/docker-compose.yaml` now uses a pgvector Postgres image.

### CI
`.github/workflows/match-engine.yml` runs on changes to `match_engine/` or `server/`: unit/API tests, Postgres + pgvector integration tests,
the retrieval eval (fails if hybrid + rerank NDCG@10 drops below 0.80; results uploaded and shown in the job summary), a Docker build,
and the server's Node tests.

### Evaluation
`python -m eval.run_eval [--resume] [--judge] [--update-readme]` — Recall@20 / NDCG@10 for BM25 vs vector vs hybrid vs hybrid + rerank,
resume-parser field accuracy (runs the real Gemini parser via `server/scripts/eval_resume_parser.js`), and explanation groundedness
(deterministic quote verification + optional LLM-as-judge).

<!-- EVAL:START -->
_Run 2026-11-17 07:06 UTC · 10 profiles · 72 jobs · 105 labelled pairs · embedder `BAAI/bge-small-en-v1.5` · reranker `Xenova/ms-marco-MiniLM-L-6-v2`_

| Retrieval | Recall@20 | NDCG@10 | p50 latency |
|---|---:|---:|---:|
| BM25 (lexical) | 1.000 | 0.925 | 398 ms |
| Vector (dense embeddings) | 0.980 | 0.884 | 465 ms |
| Hybrid (BM25 + vector, RRF) | 0.980 | 0.929 | 451 ms |
| Hybrid + cross-encoder rerank + match score | 1.000 | 0.889 | 1025 ms |

| Explanation groundedness | Value |
|---|---:|
| generator | template |
| evidence quotes verified in source | 1.0 |
| claims dropped by verifier | 0 |
<!-- EVAL:END -->

**Caveats, honestly stated:** the seed set (72 jobs, 10 profiles, 105 graded pairs in `eval/data/`) was written for this repo and labelled by the
author of the code, not by independent annotators; descriptions list skills explicitly, so BM25 is already near the ceiling and the
numbers saturate. Treat them as a regression check, not a benchmark. To make them meaningful, label pairs drawn from the real ingested
`jobs` table. Resume-parsing and LLM-judge rows only appear when `GEMINI_API_KEY` is set.

### Tests
```bash
cd match_engine && pytest -q                       # unit + API tests (offline, hash embedder)
TEST_DATABASE_URL=postgresql://... pytest -q       # + Postgres/pgvector integration tests
cd server && npm test                              # JS fallback scorer + RapidAPI normalisation
```

### Fixes to existing features
- `/api/peer-meetings` and `/api/voice-resume` routes existed but were never mounted (client calls 404'd) — now mounted.
- Language switcher called `PUT /api/users/profile`, which did not exist; added it and `native_language` saving.
- Roadmap generation used the retired `gemini-pro` model; now `gemini-2.5-flash` (override with `GEMINI_MODEL`).
- Removed a hardcoded RapidAPI key from `routes/jobs.js` and an unauthenticated `/debug/test-match` endpoint.
- "Market trends (Adzuna)" was listed as done but no Adzuna code existed; it is now real via the ingestion pipeline.

---

## 🏗️ System Architecture & Database Design

### 1. System Architecture Pipeline
![Architecture Diagram](./career_path_engine_pipeline_2026_02_20_062754_SU3JEIB0Y5.png)

### 2. Database Schema Design
![Database Diagram](./career_path_engine_pipeline_2026_02_20_065430_FUrp0SMD2T.png)

---

## 🔑 Master API Key Guide

To fully power NitiAI, you must configure the following API keys in their respective `.env` files.

| API Key | Purpose | Required In |
| :--- | :--- | :--- |
| `GEMINI_API_KEY` | Resume parsing, Career feedback, & Interview evaluation | `server/.env` & `MOCK_INTERVIEW/backend/.env` |
| `OPENROUTER_API_KEY` | Advanced LLM analysis & fallback reasoning | `server/.env` & `MOCK_INTERVIEW/backend/.env` |
| `RAPIDAPI_KEY` | LinkedIn job search data | `server/.env` |
| `ADZUNA_APP_ID/KEY` | Job market trends and salary data | `server/.env` |
| `EXECUTION_API_URL` | Safe remote code execution (Piston/GCP) | `MOCK_INTERVIEW/backend/.env` |
| `SERPAPI_API_KEY` | Targeted YouTube educational resource search | `MOCK_INTERVIEW/backend/.env` |
| `NEXT_PUBLIC_VAPI_KEY` | Voice-based AI interview interaction | `MOCK_INTERVIEW/frontend/.env.local` |
| `DATABASE_URL` | PostgreSQL (+ pgvector) connection string | `server/.env` |
| `MATCH_ENGINE_URL` / `MATCH_ENGINE_TOKEN` | Express ↔ Match Engine connection and shared secret | `server/.env` (token also for the engine) |
| `ADMIN_EMAILS` | Who can open `/admin/metrics` | `server/.env` |
| `JWT_SECRET` | Secure authentication and token signing | `server/.env` |

---

## 🚀 Getting Started

### Project Structure
- `/client` & `/server`: The core NitiAI Career Guidance platform.
- `/MOCK_INTERVIEW/frontend` & `/MOCK_INTERVIEW/backend`: The dedicated Interview Arena.
- `/match_engine`: Python/FastAPI matching, ingestion, agent and eval service.

### 1. Setup NitiAI Core
**Backend (`/server`):**
```bash
cd server && npm install
# Setup .env with GEMINI, RAPIDAPI, ADZUNA, DATABASE_URL
npm run dev
```

**Frontend (`/client`):**
```bash
cd client && npm install
# Setup .env with NEXT_PUBLIC_API_URL
npm run dev
```

### 2. Setup Mock Interview Arena
**Backend (`/MOCK_INTERVIEW/backend`):**
```bash
cd MOCK_INTERVIEW/backend && npm install
# Setup .env with GEMINI, OPENROUTER, SERPAPI, EXECUTION_API_URL
npm start
```

**Frontend (`/MOCK_INTERVIEW/frontend`):**
```bash
cd MOCK_INTERVIEW/frontend && npm install
# Setup .env.local with VAPI, OPENROUTER, SERPAPI
npm run dev
```

---

## 🛠️ Tech Stack
- **Frontend**: Next.js 14, Tailwind CSS, Framer Motion, Socket.io, Lucide React.
- **Backend**: Node.js, Express, PostgreSQL, Socket.io, JWT.
- **Match Engine**: Python, FastAPI, Pydantic, psycopg 3, pgvector, fastembed (ONNX bge-small + ms-marco cross-encoder), APScheduler.
- **AI/Cloud**: Google Gemini, OpenRouter, Vapi AI, GCP Compute Engine.

---
Developed with ❤️ for NitiAI
