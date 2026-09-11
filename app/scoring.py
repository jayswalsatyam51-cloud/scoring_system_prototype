"""Deterministic UTI scoring + checkout policy (secure default without LLM)."""

from __future__ import annotations

from typing import Any


def _clamp(n: float, lo: float = 0, hi: float = 900) -> int:
    return int(max(lo, min(hi, round(n))))


def compute_uti_score(customer: dict[str, Any]) -> dict[str, Any]:
    """User Trust Index out of 900 (CIBIL-like banding)."""
    total = max(int(customer.get("total_orders", 0)), 1)
    rto = int(customer.get("rto_count", 0))
    returns = int(customer.get("return_count", 0))
    fake = int(customer.get("fake_return_flags", 0))
    prepaid = int(customer.get("prepaid_orders", 0))
    on_time = int(customer.get("on_time_payments", 0))
    rev_neg = int(customer.get("reverse_negative_feedback", 0))

    rto_rate = rto / total
    return_rate = returns / total
    prepaid_rate = prepaid / total
    pay_rate = on_time / total

    score = 750.0
    score -= rto_rate * 280
    score -= return_rate * 160
    score -= fake * 55
    score -= rev_neg * 25
    score += prepaid_rate * 80
    score += pay_rate * 40
    # slight trust for volume with clean history
    if total >= 20 and rto_rate < 0.08 and fake == 0:
        score += 25

    final = _clamp(score)
    band = _band(final)
    breakdown = {
        "base": 750,
        "rto_penalty": round(-rto_rate * 280, 1),
        "return_penalty": round(-return_rate * 160, 1),
        "fraud_penalty": -fake * 55,
        "reverse_feedback_penalty": -rev_neg * 25,
        "prepaid_bonus": round(prepaid_rate * 80, 1),
        "payment_bonus": round(pay_rate * 40, 1),
    }
    return {
        "score": final,
        "max": 900,
        "band": band,
        "rto_rate": round(rto_rate, 3),
        "return_rate": round(return_rate, 3),
        "breakdown": breakdown,
        "label": _label(band),
    }


def _band(score: int) -> str:
    if score >= 800:
        return "A"
    if score >= 700:
        return "B"
    if score >= 600:
        return "C"
    if score >= 500:
        return "D"
    return "E"


def _label(band: str) -> str:
    return {
        "A": "Low return risk · Verified recipient",
        "B": "Generally reliable",
        "C": "Moderate risk · Prefer prepaid",
        "D": "High risk · Gate COD",
        "E": "Severe risk · Block COD / manual review",
    }[band]


def recommend_checkout_policy(uti: dict[str, Any]) -> dict[str, Any]:
    band = uti["band"]
    if band == "A":
        return {
            "cod_allowed": True,
            "otp_delivery_required": False,
            "prepaid_nudge": False,
            "action": "Allow COD · standard delivery",
        }
    if band == "B":
        return {
            "cod_allowed": True,
            "otp_delivery_required": True,
            "prepaid_nudge": False,
            "action": "Allow COD with OTP delivery verification",
        }
    if band == "C":
        return {
            "cod_allowed": False,
            "otp_delivery_required": True,
            "prepaid_nudge": True,
            "action": "Prepaid preferred · OTP mandatory if COD exception",
        }
    return {
        "cod_allowed": False,
        "otp_delivery_required": True,
        "prepaid_nudge": True,
        "action": "Block COD · prepaid only · fraud review queue",
    }


def compute_delivery_score(rider: dict[str, Any]) -> dict[str, Any]:
    deliveries = max(int(rider.get("deliveries", 0)), 1)
    incidents = int(rider.get("incidents", 0))
    otp_ok = int(rider.get("otp_success", 0))
    score = 850 - (incidents / deliveries) * 400 + (otp_ok / deliveries) * 40
    final = _clamp(score)
    return {"score": final, "max": 900, "band": _band(final), "incidents": incidents}


def compute_manufacturer_score(mfr: dict[str, Any]) -> dict[str, Any]:
    orders = max(int(mfr.get("orders_fulfilled", 0)), 1)
    quality = int(mfr.get("quality_flags", 0))
    on_time = float(mfr.get("on_time_rate", 0.9))
    score = 780 - (quality / orders) * 300 + on_time * 80
    final = _clamp(score)
    return {"score": final, "max": 900, "band": _band(final), "quality_flags": quality}


def apply_reverse_feedback(customer: dict[str, Any], tag: str, rating: int) -> dict[str, Any]:
    c = dict(customer)
    fraud_tags = {"suspected_fraud", "empty_box", "tampered", "chronic_rto"}
    if tag in fraud_tags or rating <= 2:
        c["reverse_negative_feedback"] = int(c.get("reverse_negative_feedback", 0)) + 1
        if tag in {"empty_box", "tampered", "suspected_fraud"}:
            c["fake_return_flags"] = int(c.get("fake_return_flags", 0)) + 1
    return c
