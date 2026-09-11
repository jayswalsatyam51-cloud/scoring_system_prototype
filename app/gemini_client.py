"""Gemini integration for dispute narrative + secondary recommendation."""

from __future__ import annotations

import json
import os
import re
from typing import Any

import httpx

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
GEMINI_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
)


def _extract_json(text: str) -> dict[str, Any] | None:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


async def analyze_dispute_with_gemini(payload: dict[str, Any]) -> dict[str, Any]:
    """Ask Gemini to explain attribution; rules remain source of truth for liability."""
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        return {
            "enabled": False,
            "summary": "GEMINI_API_KEY not set — using rule-based attribution only.",
            "recommendation": payload.get("rule_attribution"),
            "confidence": 0.0,
            "rationale": [
                "Deterministic rules decided liability.",
                "Set GEMINI_API_KEY on Render to enable AI narrative.",
            ],
        }

    prompt = f"""
You are Meesho's Return Shield Trust Agent for a C2M (consumer-to-manufacturer) marketplace in India.
Rule-based attribution already decided: {payload.get("rule_attribution")}.
Do NOT invent facts. Explain clearly for a manufacturer dashboard.

Return ONLY valid JSON with keys:
- summary (string, max 280 chars)
- recommendation (one of: manufacturer, meesho_or_delivery, customer_behavior)
- confidence (0 to 1)
- rationale (array of 2-4 short strings)
- manufacturer_message (one short sentence reassuring or instructing the factory)

Case data:
{json.dumps(payload, indent=2)}
"""

    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 512,
            "responseMimeType": "application/json",
        },
    }

    try:
        async with httpx.AsyncClient(timeout=25.0) as client:
            resp = await client.post(
                GEMINI_URL,
                params={"key": api_key},
                json=body,
                headers={"Content-Type": "application/json"},
            )
        if resp.status_code >= 400:
            return {
                "enabled": True,
                "error": f"Gemini HTTP {resp.status_code}",
                "summary": "AI analysis unavailable; rule-based result stands.",
                "recommendation": payload.get("rule_attribution"),
                "confidence": 0.0,
                "rationale": [resp.text[:200]],
            }
        data = resp.json()
        text = (
            data.get("candidates", [{}])[0]
            .get("content", {})
            .get("parts", [{}])[0]
            .get("text", "")
        )
        parsed = _extract_json(text) or {}
        # Force recommendation to stay aligned with rules unless empty
        parsed["enabled"] = True
        parsed["recommendation"] = payload.get("rule_attribution")
        parsed.setdefault("summary", text[:280] if text else "Analyzed.")
        parsed.setdefault("confidence", 0.7)
        parsed.setdefault("rationale", [])
        parsed.setdefault(
            "manufacturer_message",
            "Rule-based Return Shield attribution applied; AI explanation attached.",
        )
        return parsed
    except Exception as exc:  # noqa: BLE001 — demo resilience
        return {
            "enabled": True,
            "error": str(exc)[:200],
            "summary": "AI call failed; rule-based attribution unchanged.",
            "recommendation": payload.get("rule_attribution"),
            "confidence": 0.0,
            "rationale": ["Fallback to deterministic Return Shield rules."],
        }
