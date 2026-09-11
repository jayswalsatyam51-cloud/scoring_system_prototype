"""Simple in-memory store with seed data (demo / Render ephemeral)."""

from __future__ import annotations

import json
import os
import threading
from copy import deepcopy
from typing import Any

_lock = threading.Lock()
_STORE: "Store | None" = None

SEED_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "seed.json")


class Store:
    def __init__(self) -> None:
        self.customers: dict[str, dict[str, Any]] = {}
        self.manufacturers: dict[str, dict[str, Any]] = {}
        self.riders: dict[str, dict[str, Any]] = {}
        self.feedback: list[dict[str, Any]] = []
        self.disputes: list[dict[str, Any]] = []

    def seed_if_empty(self) -> None:
        with _lock:
            if self.customers:
                return
            if os.path.exists(SEED_PATH):
                with open(SEED_PATH, encoding="utf-8") as f:
                    raw = json.load(f)
            else:
                raw = {"customers": [], "manufacturers": [], "riders": []}
            for c in raw.get("customers", []):
                self.customers[c["customer_id"]] = c
            for m in raw.get("manufacturers", []):
                self.manufacturers[m["manufacturer_id"]] = m
            for r in raw.get("riders", []):
                self.riders[r["delivery_partner_id"]] = r

    def list_customers(self) -> list[dict[str, Any]]:
        return [deepcopy(v) for v in self.customers.values()]

    def list_manufacturers(self) -> list[dict[str, Any]]:
        return [deepcopy(v) for v in self.manufacturers.values()]

    def list_riders(self) -> list[dict[str, Any]]:
        rows = [deepcopy(v) for v in self.riders.values()]
        rows.sort(key=lambda r: (r.get("carrier") or "Other", r.get("name") or ""))
        return rows

    def riders_by_carrier(self) -> dict[str, list[dict[str, Any]]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for r in self.list_riders():
            carrier = (r.get("carrier") or "Other").strip() or "Other"
            grouped.setdefault(carrier, []).append(r)
        return grouped

    def list_disputes(self) -> list[dict[str, Any]]:
        return list(reversed(self.disputes[-50:]))

    def get_customer(self, cid: str) -> dict[str, Any] | None:
        c = self.customers.get(cid)
        return deepcopy(c) if c else None

    def get_manufacturer(self, mid: str) -> dict[str, Any] | None:
        m = self.manufacturers.get(mid)
        return deepcopy(m) if m else None

    def get_rider(self, rid: str) -> dict[str, Any] | None:
        r = self.riders.get(rid)
        return deepcopy(r) if r else None

    def upsert_customer(self, data: dict[str, Any]) -> None:
        with _lock:
            self.customers[data["customer_id"]] = deepcopy(data)

    def upsert_manufacturer(self, data: dict[str, Any]) -> None:
        with _lock:
            self.manufacturers[data["manufacturer_id"]] = deepcopy(data)

    def upsert_rider(self, data: dict[str, Any]) -> None:
        with _lock:
            self.riders[data["delivery_partner_id"]] = deepcopy(data)

    def add_feedback(self, entry: dict[str, Any]) -> None:
        with _lock:
            self.feedback.append(entry)

    def feedback_for_customer(self, cid: str) -> list[dict[str, Any]]:
        return [f for f in self.feedback if f.get("customer_id") == cid]

    def add_dispute(self, entry: dict[str, Any]) -> None:
        with _lock:
            self.disputes.append(entry)


def get_store() -> Store:
    global _STORE
    if _STORE is None:
        _STORE = Store()
    return _STORE
