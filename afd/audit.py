"""Audit log: attribution instead of permissions.

The product has flat access (spec section 2): every teammate can edit the
ontology and the agent rules. Safety comes from recording who changed what,
when, with the before/after values, plus versioning and one-click rollback.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from .store import LocalStore


def now_iso() -> str:
    """UTC timestamp used for every created_at/updated_at in the app."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def record(store: LocalStore, actor: str, action: str, target: str, before: Any = None,
           after: Any = None, note: str = "") -> dict:
    """Append one audit row. `target` is e.g. "ontology@v3" or "config@v2"."""
    return store.insert("audit_log", {
        "audit_id": uuid.uuid4().hex[:12],
        "created_at": now_iso(),
        "actor": actor,
        "action": action,
        "target": target,
        "before": before,
        "after": after,
        "note": note,
    })
