"""Meesho UTI (User Trust Index) Scoring System — FastAPI app."""

from __future__ import annotations

import os
import secrets
from contextlib import asynccontextmanager
from typing import Any, Literal

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from app.evidence import is_valid_evidence_id, resolve_evidence_path, save_evidence_files
from app.gemini_client import analyze_dispute_with_gemini
from app.scoring import (
    apply_reverse_feedback,
    compute_delivery_score,
    compute_manufacturer_score,
    compute_uti_score,
    recommend_checkout_policy,
)
from app.store import Store, get_store

APP_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(APP_DIR)


@asynccontextmanager
async def lifespan(app: FastAPI):
    store = get_store()
    store.seed_if_empty()
    yield


app = FastAPI(
    title="Meesho UTI Trust Score",
    description="C2M Return Shield + User Trust Index demo (Render-ready)",
    version="1.1.0",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory=os.path.join(ROOT_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(ROOT_DIR, "templates"))


def store_dep() -> Store:
    return get_store()


def _dispute_page_ctx(
    request: Request, store: Store, result: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {
        "request": request,
        "customers": store.list_customers(),
        "manufacturers": store.list_manufacturers(),
        "riders": store.list_riders(),
        "riders_by_carrier": store.riders_by_carrier(),
        "gemini_configured": bool(os.getenv("GEMINI_API_KEY")),
        "result": result,
    }


# ─── API models ───────────────────────────────────────────────

class CustomerSignals(BaseModel):
    customer_id: str = Field(..., min_length=1, max_length=64)
    name: str = Field(default="Customer", max_length=80)
    total_orders: int = Field(ge=0, le=100000, default=10)
    rto_count: int = Field(ge=0, le=100000, default=0)
    return_count: int = Field(ge=0, le=100000, default=0)
    fake_return_flags: int = Field(ge=0, le=1000, default=0)
    prepaid_orders: int = Field(ge=0, le=100000, default=0)
    on_time_payments: int = Field(ge=0, le=100000, default=0)
    reverse_negative_feedback: int = Field(ge=0, le=1000, default=0)


class DisputeRequest(BaseModel):
    order_id: str = Field(..., min_length=1, max_length=64)
    customer_id: str
    manufacturer_id: str
    delivery_partner_id: str
    category: str = "apparel"
    return_reason: str = Field(..., min_length=3, max_length=500)
    customer_notes: str = Field(default="", max_length=1000)
    package_seal_intact: bool = True
    photos_show_damage: bool = False
    empty_box_reported: bool = False
    delivery_otp_verified: bool = True
    # Pre-uploaded evidence from POST /api/dispute/evidence
    evidence: list[dict[str, Any]] = Field(default_factory=list, max_length=5)


class ReverseFeedbackRequest(BaseModel):
    customer_id: str
    manufacturer_id: str
    rating: Literal[1, 2, 3, 4, 5]
    tag: Literal[
        "genuine_return",
        "size_issue",
        "quality_issue",
        "suspected_fraud",
        "empty_box",
        "tampered",
        "chronic_rto",
    ]
    comment: str = Field(default="", max_length=500)


# ─── Pages ────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def home(request: Request, store: Store = Depends(store_dep)):
    customers = store.list_customers()
    scored = [
        {
            **c,
            "uti": compute_uti_score(c),
            "policy": recommend_checkout_policy(compute_uti_score(c)),
        }
        for c in customers
    ]
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "customers": scored,
            "manufacturers": store.list_manufacturers(),
            "riders": store.list_riders(),
            "riders_by_carrier": store.riders_by_carrier(),
            "disputes": store.list_disputes()[:8],
            "gemini_configured": bool(os.getenv("GEMINI_API_KEY")),
        },
    )


@app.get("/customer/{customer_id}", response_class=HTMLResponse)
async def customer_page(request: Request, customer_id: str, store: Store = Depends(store_dep)):
    c = store.get_customer(customer_id)
    if not c:
        raise HTTPException(404, "Customer not found")
    uti = compute_uti_score(c)
    return templates.TemplateResponse(
        "customer.html",
        {
            "request": request,
            "customer": c,
            "uti": uti,
            "policy": recommend_checkout_policy(uti),
            "feedback": store.feedback_for_customer(customer_id),
        },
    )


@app.get("/dispute", response_class=HTMLResponse)
async def dispute_form(request: Request, store: Store = Depends(store_dep)):
    return templates.TemplateResponse("dispute.html", _dispute_page_ctx(request, store))


@app.post("/dispute", response_class=HTMLResponse)
async def dispute_submit(
    request: Request,
    order_id: str = Form(...),
    customer_id: str = Form(...),
    manufacturer_id: str = Form(...),
    delivery_partner_id: str = Form(...),
    category: str = Form("apparel"),
    return_reason: str = Form(...),
    customer_notes: str = Form(""),
    package_seal_intact: str = Form("yes"),
    photos_show_damage: str = Form("no"),
    empty_box_reported: str = Form("no"),
    delivery_otp_verified: str = Form("yes"),
    evidence: list[UploadFile] | None = File(default=None),
    store: Store = Depends(store_dep),
):
    evidence_meta = await save_evidence_files(evidence or [])
    photos_flag = photos_show_damage == "yes" or any(e["kind"] == "image" for e in evidence_meta)
    payload = DisputeRequest(
        order_id=order_id.strip()[:64],
        customer_id=customer_id,
        manufacturer_id=manufacturer_id,
        delivery_partner_id=delivery_partner_id,
        category=category,
        return_reason=return_reason.strip()[:500],
        customer_notes=customer_notes.strip()[:1000],
        package_seal_intact=package_seal_intact == "yes",
        photos_show_damage=photos_flag,
        empty_box_reported=empty_box_reported == "yes",
        delivery_otp_verified=delivery_otp_verified == "yes",
        evidence=evidence_meta,
    )
    result = await _run_dispute(payload, store)
    return templates.TemplateResponse("dispute.html", _dispute_page_ctx(request, store, result))


# ─── JSON APIs ────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "meesho-uti-scoring",
        "gemini": bool(os.getenv("GEMINI_API_KEY")),
    }


@app.post("/api/score/customer")
async def api_score_customer(body: CustomerSignals, store: Store = Depends(store_dep)):
    data = body.model_dump()
    store.upsert_customer(data)
    uti = compute_uti_score(data)
    return {"customer_id": body.customer_id, "uti": uti, "policy": recommend_checkout_policy(uti)}


@app.get("/api/score/customer/{customer_id}")
async def api_get_customer_score(customer_id: str, store: Store = Depends(store_dep)):
    c = store.get_customer(customer_id)
    if not c:
        raise HTTPException(404, "Not found")
    uti = compute_uti_score(c)
    return {
        "customer": c,
        "uti": uti,
        "policy": recommend_checkout_policy(uti),
        "delivery_context": None,
    }


@app.get("/api/delivery-partners")
async def api_delivery_partners(store: Store = Depends(store_dep)):
    return {
        "partners": store.list_riders(),
        "by_carrier": store.riders_by_carrier(),
    }


@app.post("/api/dispute/evidence")
async def api_upload_evidence(files: list[UploadFile] = File(...)):
    """Upload photo/video evidence; attach returned metadata on /api/dispute/analyze."""
    saved = await save_evidence_files(files)
    if not saved:
        raise HTTPException(400, "No valid files uploaded")
    return {"evidence": saved, "count": len(saved)}


@app.post("/api/dispute/analyze")
async def api_dispute(body: DisputeRequest, store: Store = Depends(store_dep)):
    return await _run_dispute(body, store)


@app.get("/evidence/{file_id}")
async def serve_evidence(file_id: str):
    path = resolve_evidence_path(file_id)
    media = {
        ".jpg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".mp4": "video/mp4",
        ".webm": "video/webm",
    }
    return FileResponse(
        path,
        media_type=media.get(path.suffix.lower(), "application/octet-stream"),
        filename=path.name,
        content_disposition_type="inline",
    )


@app.post("/api/feedback/reverse")
async def api_reverse_feedback(body: ReverseFeedbackRequest, store: Store = Depends(store_dep)):
    c = store.get_customer(body.customer_id)
    if not c:
        raise HTTPException(404, "Customer not found")
    entry = {
        "id": secrets.token_hex(4),
        "customer_id": body.customer_id,
        "manufacturer_id": body.manufacturer_id,
        "rating": body.rating,
        "tag": body.tag,
        "comment": body.comment[:500],
    }
    store.add_feedback(entry)
    updated = apply_reverse_feedback(c, body.tag, body.rating)
    store.upsert_customer(updated)
    uti = compute_uti_score(updated)
    return {"feedback": entry, "uti": uti, "policy": recommend_checkout_policy(uti)}


@app.get("/api/leaderboard")
async def api_leaderboard(store: Store = Depends(store_dep)):
    rows = []
    for c in store.list_customers():
        uti = compute_uti_score(c)
        rows.append({"customer_id": c["customer_id"], "name": c.get("name"), "uti": uti})
    rows.sort(key=lambda x: x["uti"]["score"], reverse=True)
    return {"leaderboard": rows}


def _sanitize_evidence(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Only keep evidence metadata that points at our stored files (no arbitrary URLs)."""
    clean: list[dict[str, Any]] = []
    for item in raw[:5]:
        if not isinstance(item, dict):
            continue
        file_id = str(item.get("id") or "").strip()
        if not is_valid_evidence_id(file_id):
            continue
        kind = item.get("kind") if item.get("kind") in ("image", "video") else "image"
        clean.append(
            {
                "id": file_id,
                "kind": kind,
                "content_type": str(item.get("content_type") or "")[:64],
                "size_bytes": int(item.get("size_bytes") or 0),
                "original_name": str(item.get("original_name") or "evidence")[:120],
                "url": f"/evidence/{file_id}",
            }
        )
    return clean


async def _run_dispute(body: DisputeRequest, store: Store) -> dict[str, Any]:
    customer = store.get_customer(body.customer_id)
    manufacturer = store.get_manufacturer(body.manufacturer_id)
    rider = store.get_rider(body.delivery_partner_id)
    if not customer or not manufacturer or not rider:
        raise HTTPException(400, "Invalid customer, manufacturer, or rider id")

    carrier = rider.get("carrier") or "Delivery partner"
    evidence = _sanitize_evidence(body.evidence or [])
    photos_show_damage = body.photos_show_damage or any(e.get("kind") == "image" for e in evidence)

    # Rule-based attribution first (deterministic, secure default)
    if body.empty_box_reported or not body.package_seal_intact:
        attribution = "meesho_or_delivery"
        liable = f"Meesho / {carrier} (tamper or empty box)"
        customer_delta = -40
        rider_delta = -35
        mfr_delta = 0
    elif photos_show_damage:
        attribution = "manufacturer"
        liable = "Manufacturer (quality / damage)"
        customer_delta = 5
        rider_delta = 0
        mfr_delta = -25
    else:
        attribution = "customer_behavior"
        liable = "Customer (change of mind / fit) — shared policy"
        customer_delta = -15
        rider_delta = 0
        mfr_delta = 0

    pre_uti = compute_uti_score(customer)
    gemini = await analyze_dispute_with_gemini(
        {
            "order_id": body.order_id,
            "category": body.category,
            "return_reason": body.return_reason,
            "customer_notes": body.customer_notes,
            "package_seal_intact": body.package_seal_intact,
            "photos_show_damage": photos_show_damage,
            "empty_box_reported": body.empty_box_reported,
            "delivery_otp_verified": body.delivery_otp_verified,
            "delivery_carrier": carrier,
            "delivery_partner_id": body.delivery_partner_id,
            "delivery_partner_name": rider.get("name"),
            "evidence_count": len(evidence),
            "evidence_kinds": [e.get("kind") for e in evidence if isinstance(e, dict)],
            "rule_attribution": attribution,
            "customer_uti_before": pre_uti,
            "customer_stats": {
                "rto_count": customer.get("rto_count"),
                "return_count": customer.get("return_count"),
                "fake_return_flags": customer.get("fake_return_flags"),
                "total_orders": customer.get("total_orders"),
            },
        }
    )

    if attribution == "meesho_or_delivery":
        customer["fake_return_flags"] = int(customer.get("fake_return_flags", 0)) + (
            1 if body.empty_box_reported else 0
        )
        rider["incidents"] = int(rider.get("incidents", 0)) + 1
    elif attribution == "manufacturer":
        manufacturer["quality_flags"] = int(manufacturer.get("quality_flags", 0)) + 1
    else:
        customer["return_count"] = int(customer.get("return_count", 0)) + 1

    store.upsert_customer(customer)
    store.upsert_manufacturer(manufacturer)
    store.upsert_rider(rider)

    post_uti = compute_uti_score(customer)
    rider_score = compute_delivery_score(rider)
    mfr_score = compute_manufacturer_score(manufacturer)
    policy = recommend_checkout_policy(post_uti)

    dispute = {
        "id": secrets.token_hex(4),
        "order_id": body.order_id,
        "attribution": attribution,
        "liable": liable,
        "customer_id": body.customer_id,
        "manufacturer_id": body.manufacturer_id,
        "delivery_partner_id": body.delivery_partner_id,
        "delivery_carrier": carrier,
        "delivery_partner_name": rider.get("name"),
        "evidence": evidence,
        "gemini": gemini,
        "uti_before": pre_uti,
        "uti_after": post_uti,
        "rider_score": rider_score,
        "manufacturer_score": mfr_score,
        "policy": policy,
        "deltas": {
            "customer": customer_delta,
            "rider": rider_delta,
            "manufacturer": mfr_delta,
        },
    }
    store.add_dispute(dispute)
    return dispute


def create_app() -> FastAPI:
    return app
