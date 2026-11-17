"""Persistent coach memory: remembered facts and recent conversation turns."""
from __future__ import annotations

from .db import conn

KINDS = ("preference", "goal", "constraint", "note")


def remember(user_id: str, kind: str, key: str, value: str) -> dict:
    if kind not in KINDS:
        kind = "note"
    with conn() as c:
        c.execute("""INSERT INTO coach_memory (user_id, kind, key, value) VALUES (%s,%s,%s,%s)
                     ON CONFLICT (user_id, kind, key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()""",
                  [user_id, kind, key.strip().lower()[:80], value.strip()[:500]])
    return {"kind": kind, "key": key, "value": value}


def forget(user_id: str, kind: str, key: str) -> int:
    with conn() as c:
        return c.execute("DELETE FROM coach_memory WHERE user_id=%s AND kind=%s AND key=%s",
                         [user_id, kind, key.strip().lower()]).rowcount or 0


def recall(user_id: str) -> list[dict]:
    with conn() as c:
        return c.execute("SELECT kind, key, value, updated_at FROM coach_memory WHERE user_id=%s ORDER BY kind, key",
                         [user_id]).fetchall()


def add_message(user_id: str, role: str, content: str) -> None:
    with conn() as c:
        c.execute("INSERT INTO coach_messages (user_id, role, content) VALUES (%s,%s,%s)", [user_id, role, content[:8000]])


def recent_messages(user_id: str, n: int = 12) -> list[dict]:
    with conn() as c:
        rows = c.execute("SELECT role, content FROM coach_messages WHERE user_id=%s ORDER BY id DESC LIMIT %s",
                         [user_id, n]).fetchall()
    return list(reversed(rows))
