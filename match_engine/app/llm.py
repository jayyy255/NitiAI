"""Minimal Gemini REST client: JSON-mode generation and function calling."""
from __future__ import annotations

import json
from typing import Any

import httpx

from .config import get_settings

BASE = "https://generativelanguage.googleapis.com/v1beta/models"


class LLMUnavailable(RuntimeError):
    pass


def available() -> bool:
    return bool(get_settings().gemini_api_key)


def _post(body: dict, timeout: float = 40.0) -> dict:
    s = get_settings()
    if not s.gemini_api_key:
        raise LLMUnavailable("GEMINI_API_KEY not set")
    r = httpx.post(f"{BASE}/{s.gemini_model}:generateContent", json=body, timeout=timeout,
                   headers={"x-goog-api-key": s.gemini_api_key})
    if r.status_code >= 400:
        raise LLMUnavailable(f"Gemini {r.status_code}: {r.text[:300]}")
    return r.json()


def generate_json(prompt: str, schema: dict | None = None, temperature: float = 0.1) -> Any:
    cfg: dict[str, Any] = {"temperature": temperature, "responseMimeType": "application/json"}
    if schema:
        cfg["responseSchema"] = schema
    data = _post({"contents": [{"role": "user", "parts": [{"text": prompt}]}], "generationConfig": cfg})
    text = "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
    return json.loads(text)


def chat_with_tools(system: str, contents: list[dict], tools: list[dict]) -> dict:
    """One model turn. Returns the candidate content ({role, parts})."""
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": contents,
        "tools": [{"functionDeclarations": tools}],
        "generationConfig": {"temperature": 0.3},
    }
    data = _post(body)
    return data["candidates"][0]["content"]
