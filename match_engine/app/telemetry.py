"""Request latency logging (structured log line + request_logs table) and summary stats."""
from __future__ import annotations

import json
import logging
import queue
import threading
import time

from starlette.middleware.base import BaseHTTPMiddleware

from .db import conn, db_available

log = logging.getLogger("telemetry")
_q: "queue.Queue[tuple]" = queue.Queue(maxsize=10_000)


def _writer():
    while True:
        batch = [_q.get()]
        while not _q.empty() and len(batch) < 200:
            batch.append(_q.get_nowait())
        try:
            with conn() as c:
                with c.cursor() as cur:
                    cur.executemany("INSERT INTO request_logs (route, method, status, latency_ms) VALUES (%s,%s,%s,%s)", batch)
        except Exception as e:
            log.debug("request log write failed: %s", e)


_started = False


def start_writer():
    global _started
    if not _started and db_available():
        threading.Thread(target=_writer, daemon=True, name="request-log-writer").start()
        _started = True


class LatencyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        t0 = time.perf_counter()
        status = 500
        try:
            resp = await call_next(request)
            status = resp.status_code
            return resp
        finally:
            ms = (time.perf_counter() - t0) * 1000
            route = request.scope.get("route")
            path = getattr(route, "path", request.url.path)
            log.info(json.dumps({"route": path, "method": request.method, "status": status, "ms": round(ms, 1)}))
            if _started and path not in ("/health",):
                try:
                    _q.put_nowait((path, request.method, status, ms))
                except queue.Full:
                    pass


def summary(hours: int = 24) -> dict:
    with conn() as c:
        routes = c.execute(
            """SELECT route, method, count(*) AS n,
                      round(percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms)::numeric, 1) AS p50,
                      round(percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms)::numeric, 1) AS p95,
                      round(percentile_cont(0.99) WITHIN GROUP (ORDER BY latency_ms)::numeric, 1) AS p99,
                      sum((status >= 500)::int) AS errors
               FROM request_logs WHERE created_at > now() - make_interval(hours => %s)
               GROUP BY route, method ORDER BY n DESC""", [hours]).fetchall()
        hourly = c.execute(
            """SELECT date_trunc('hour', created_at) AS hour, count(*) AS n,
                      round(percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms)::numeric, 1) AS p95
               FROM request_logs WHERE created_at > now() - make_interval(hours => %s)
               GROUP BY 1 ORDER BY 1""", [hours]).fetchall()
        jobs = c.execute("""SELECT count(*) FILTER (WHERE is_active) AS active, count(*) AS total,
                                   count(*) FILTER (WHERE is_active AND embedded_hash IS DISTINCT FROM content_hash) AS pending_embed
                            FROM jobs""").fetchone()
        last_run = c.execute("SELECT * FROM ingest_runs ORDER BY id DESC LIMIT 1").fetchone()
        events = c.execute("""SELECT event, count(*) AS n FROM job_interactions
                              WHERE created_at > now() - make_interval(hours => %s) GROUP BY event""", [hours]).fetchall()
    to_f = lambda r: {k: (float(v) if hasattr(v, "is_finite") else v) for k, v in r.items()}  # noqa: E731
    return {"window_hours": hours, "routes": [to_f(r) for r in routes],
            "hourly": [{"hour": r["hour"].isoformat(), "n": r["n"], "p95": float(r["p95"] or 0)} for r in hourly],
            "jobs": jobs, "last_ingest": last_run, "events": {r["event"]: r["n"] for r in events}}
