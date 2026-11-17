"""Offline evaluation harness for the Match Engine.

  python -m eval.run_eval                      # retrieval metrics only (no network, no DB)
  python -m eval.run_eval --resume --judge     # + resume-parser accuracy and LLM-as-judge groundedness
  python -m eval.run_eval --update-readme      # write the results table into the repo README

Retrieval: Recall@20 and NDCG@10 for BM25, vector, hybrid (RRF) and hybrid + rerank, over
eval/data/{jobs,profiles,labels}.jsonl. Unlabelled jobs count as grade 0.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent))

from app import llm  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.explain import explain, verify_evidence  # noqa: E402
from app.models import Job, Profile  # noqa: E402
from app.retrieval import MemoryBackend, search  # noqa: E402
from app.skills import canonical_set  # noqa: E402

DATA = ROOT / "data"
MODES = ["bm25", "vector", "hybrid", "hybrid_rerank"]
REL_THRESHOLD = 2  # grade >= 2 counts as relevant for recall


def load_jsonl(p: Path) -> list[dict]:
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


def ndcg_at(ranked: list[str], grades: dict[str, int], k: int) -> float:
    dcg = sum((2 ** grades.get(d, 0) - 1) / math.log2(i + 2) for i, d in enumerate(ranked[:k]))
    ideal = sorted(grades.values(), reverse=True)[:k]
    idcg = sum((2 ** g - 1) / math.log2(i + 2) for i, g in enumerate(ideal))
    return dcg / idcg if idcg else 0.0


def recall_at(ranked: list[str], grades: dict[str, int], k: int) -> float:
    rel = {d for d, g in grades.items() if g >= REL_THRESHOLD}
    return len(rel & set(ranked[:k])) / len(rel) if rel else 0.0


def eval_retrieval(jobs, profiles, labels) -> dict:
    backend = MemoryBackend(jobs)
    by_user: dict[str, dict[str, int]] = {}
    for lab in labels:
        by_user.setdefault(lab["user_id"], {})[lab["job_id"]] = lab["grade"]
    out = {}
    for mode in MODES:
        rec, nd, lat = [], [], []
        for p in profiles:
            grades = by_user.get(p.user_id, {})
            if not grades:
                continue
            t0 = time.perf_counter()
            res = search(backend, p, k=50, mode=mode)
            lat.append((time.perf_counter() - t0) * 1000)
            ranked = [r.job.id for r in res]
            rec.append(recall_at(ranked, grades, 20))
            nd.append(ndcg_at(ranked, grades, 10))
        out[mode] = {"recall@20": round(statistics.mean(rec), 4), "ndcg@10": round(statistics.mean(nd), 4),
                     "p50_ms": round(statistics.median(lat), 1), "queries": len(rec)}
    return out


# ------------------------------------------------------------------ resume parsing

def _tok(s):
    return {t for t in (s or "").lower().replace("—", " ").split() if t}


def eval_resume_parser() -> dict:
    node = shutil.which("node")
    server = ROOT.parent.parent / "server"
    if not node or not (server / "node_modules").exists():
        return {"skipped": "node or server/node_modules not available"}
    if not (os.environ.get("GEMINI_API_KEY") or get_settings().gemini_api_key):
        return {"skipped": "GEMINI_API_KEY not set"}
    out_path = ROOT / "out" / "parsed_resumes.json"
    out_path.parent.mkdir(exist_ok=True)
    subprocess.run([node, "scripts/eval_resume_parser.js", str(ROOT / "resumes"), str(out_path)], cwd=server,
                   check=True, timeout=900)
    parsed = json.loads(out_path.read_text())
    gold = json.loads((ROOT / "resumes" / "gold.json").read_text())
    fields = {"name": [], "email": [], "title": [], "years_of_experience": []}
    tp = fp = fn = 0
    for rid, g in gold.items():
        p = parsed.get(rid) or {}
        fields["name"].append((p.get("name") or "").strip().lower() == g["name"].lower())
        fields["email"].append((p.get("email") or "").strip().lower() == g["email"].lower())
        if g["title"] is None:
            fields["title"].append(not p.get("title") or "student" in (p.get("title") or "").lower())
        else:
            gt, pt = _tok(g["title"]), _tok(p.get("title"))
            fields["title"].append(len(gt & pt) / len(gt) >= 0.5)
        try:
            fields["years_of_experience"].append(abs(float(p.get("years_of_experience") or 0) - g["years_of_experience"]) <= 1)
        except (TypeError, ValueError):
            fields["years_of_experience"].append(False)
        ps = {s.lower() for s in canonical_set(p.get("technical_skills") or [])}
        gs = {s.lower() for s in canonical_set(g["technical_skills"])}
        tp += len(ps & gs)
        fp += len(ps - gs)
        fn += len(gs - ps)
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    return {"resumes": len(gold), **{f"{k}_acc": round(sum(v) / len(v), 3) for k, v in fields.items()},
            "skills_precision": round(prec, 3), "skills_recall": round(rec, 3),
            "skills_f1": round(2 * prec * rec / (prec + rec), 3) if prec + rec else 0.0}


# ------------------------------------------------------------------ groundedness

JUDGE_SCHEMA = {"type": "OBJECT", "properties": {"verdicts": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
    "claim": {"type": "STRING"}, "supported": {"type": "BOOLEAN"}}, "required": ["claim", "supported"]}}},
    "required": ["verdicts"]}


def judge(profile: Profile, job: Job, claims: list[str]) -> list[bool]:
    prompt = f"""You are a strict fact-checker. For each claim, answer supported=true only if the claim is directly
stated by or follows from the CANDIDATE and JOB texts. Anything not in the texts is unsupported.

CANDIDATE:
<<<{profile.as_text()}>>>
JOB:
<<<{job.as_text()}>>>
CLAIMS:
{json.dumps(claims, indent=1)}"""
    out = llm.generate_json(prompt, JUDGE_SCHEMA, temperature=0.0)
    v = [bool(x.get("supported")) for x in out.get("verdicts", [])]
    return (v + [False] * len(claims))[: len(claims)]


def eval_groundedness(jobs, profiles, use_judge: bool, top: int = 3) -> dict:
    backend = MemoryBackend(jobs)
    det_ok = det_total = j_ok = j_total = dropped = 0
    generator = "template"
    for p in profiles:
        for r in search(backend, p, k=top, mode="hybrid_rerank"):
            exp = explain(p, r.job, r.match, use_llm=True)
            generator = exp.generator
            dropped += exp.dropped_claims
            ptext, jtext = p.as_text(), r.job.as_text()
            for ev in exp.strengths:
                det_total += 1
                det_ok += verify_evidence(ev, ptext, jtext)
            if use_judge and llm.available():
                claims = [exp.headline] + [f"Candidate has {e.skill} and the job asks for it" for e in exp.strengths] \
                         + [f"Candidate lacks {g}, which the job asks for" for g in exp.gaps] + exp.concerns
                try:
                    v = judge(p, r.job, claims)
                    j_ok += sum(v)
                    j_total += len(v)
                except Exception as e:  # judge outage shouldn't kill the run
                    print("judge failed:", e, file=sys.stderr)
    out = {"explanations": len(profiles) * top, "generator": generator,
           "evidence_verified": round(det_ok / det_total, 3) if det_total else None,
           "claims_dropped_by_verifier": dropped}
    if use_judge:
        out["llm_judge_supported"] = round(j_ok / j_total, 3) if j_total else None
        out["llm_judge_claims"] = j_total
        if not llm.available():
            out["llm_judge_supported"] = "skipped: GEMINI_API_KEY not set"
    return out


# ------------------------------------------------------------------ reporting

def to_markdown(res: dict) -> str:
    names = {"bm25": "BM25 (lexical)", "vector": "Vector (dense embeddings)", "hybrid": "Hybrid (BM25 + vector, RRF)",
             "hybrid_rerank": "Hybrid + cross-encoder rerank + match score"}
    lines = [f"_Run {res['run_at']} · {res['dataset']['profiles']} profiles · {res['dataset']['jobs']} jobs · "
             f"{res['dataset']['labels']} labelled pairs · embedder `{res['config']['embedder']}` · reranker `{res['config']['reranker']}`_",
             "", "| Retrieval | Recall@20 | NDCG@10 | p50 latency |", "|---|---:|---:|---:|"]
    for m in MODES:
        r = res["retrieval"][m]
        lines.append(f"| {names[m]} | {r['recall@20']:.3f} | {r['ndcg@10']:.3f} | {r['p50_ms']:.0f} ms |")
    rp = res.get("resume_parsing")
    if rp:
        lines += ["", "| Resume parsing (Gemini) | Value |", "|---|---:|"]
        if "skipped" in rp:
            lines.append(f"| skipped | {rp['skipped']} |")
        else:
            for k in ("name_acc", "email_acc", "title_acc", "years_of_experience_acc", "skills_precision", "skills_recall", "skills_f1"):
                lines.append(f"| {k.replace('_', ' ')} | {rp[k]:.3f} |")
    g = res.get("groundedness")
    if g:
        lines += ["", "| Explanation groundedness | Value |", "|---|---:|",
                  f"| generator | {g['generator']} |", f"| evidence quotes verified in source | {g['evidence_verified']} |",
                  f"| claims dropped by verifier | {g['claims_dropped_by_verifier']} |"]
        if "llm_judge_supported" in g:
            lines.append(f"| LLM-as-judge: claims supported | {g['llm_judge_supported']} |")
    return "\n".join(lines)


def update_readme(md: str, path: Path):
    start, end = "<!-- EVAL:START -->", "<!-- EVAL:END -->"
    text = path.read_text(encoding="utf-8")
    if start not in text:
        return False
    a, b = text.index(start) + len(start), text.index(end)
    path.write_text(text[:a] + "\n" + md + "\n" + text[b:], encoding="utf-8")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--resume", action="store_true", help="evaluate the Gemini resume parser (needs GEMINI_API_KEY)")
    ap.add_argument("--judge", action="store_true", help="LLM-as-judge groundedness (needs GEMINI_API_KEY)")
    ap.add_argument("--update-readme", action="store_true")
    ap.add_argument("--min-ndcg", type=float, default=None, help="exit 1 if hybrid_rerank NDCG@10 is below this (CI gate)")
    args = ap.parse_args()

    jobs = [Job(**j) for j in load_jsonl(DATA / "jobs.jsonl")]
    profiles = [Profile(**p) for p in load_jsonl(DATA / "profiles.jsonl")]
    labels = load_jsonl(DATA / "labels.jsonl")
    s = get_settings()
    res = {"run_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
           "dataset": {"jobs": len(jobs), "profiles": len(profiles), "labels": len(labels)},
           "config": {"embedder": s.embedding_model if s.embedder != "hash" else "hash",
                      "reranker": s.rerank_model if (s.reranker != "none" and s.embedder != "hash") else "cosine"},
           "retrieval": eval_retrieval(jobs, profiles, labels)}
    if args.resume:
        res["resume_parsing"] = eval_resume_parser()
    res["groundedness"] = eval_groundedness(jobs, profiles, args.judge)

    (ROOT / "results.json").write_text(json.dumps(res, indent=2))
    md = to_markdown(res)
    (ROOT / "results.md").write_text(md + "\n", encoding="utf-8")
    print(md)
    if args.update_readme:
        ok = update_readme(md, ROOT.parent.parent / "README.md")
        print("README updated" if ok else "README markers not found", file=sys.stderr)
    if args.min_ndcg is not None and res["retrieval"]["hybrid_rerank"]["ndcg@10"] < args.min_ndcg:
        print(f"FAIL: hybrid_rerank NDCG@10 below {args.min_ndcg}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
