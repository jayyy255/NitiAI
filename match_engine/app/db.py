"""Postgres connection pool (psycopg 3) with pgvector registered on every connection."""
from __future__ import annotations

import atexit
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from pgvector.psycopg import register_vector
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .config import get_settings

MIGRATIONS = Path(__file__).parent / "migrations"


def _clean_url(url: str) -> str:
    u = urlparse(url)
    q = [(k, v) for k, v in parse_qsl(u.query) if k != "uselibpqcompat"]
    if not any(k == "sslmode" for k, _ in q) and any(h in (u.hostname or "") for h in ("neon.tech", "supabase", "render.com")):
        q.append(("sslmode", "require"))
    return urlunparse(u._replace(query=urlencode(q)))


def _configure(c):
    try:
        register_vector(c)
    except Exception:
        # extension not created yet (first boot, before migrate)
        pass


@lru_cache(maxsize=1)
def get_pool() -> ConnectionPool:
    s = get_settings()
    if not s.database_url:
        raise RuntimeError("DATABASE_URL is not set")
    pool = ConnectionPool(_clean_url(s.database_url), min_size=1, max_size=10, configure=_configure,
                          kwargs={"row_factory": dict_row, "autocommit": True}, open=True)
    atexit.register(pool.close)
    return pool


@contextmanager
def conn():
    with get_pool().connection() as c:
        yield c


def migrate() -> None:
    with conn() as c:
        for f in sorted(MIGRATIONS.glob("*.sql")):
            c.execute(f.read_text(encoding="utf-8"))
    # connections opened before the extension existed lack the vector adapter
    get_pool().check()


def db_available() -> bool:
    return bool(get_settings().database_url)
