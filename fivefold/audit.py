from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from fivefold.models import AuditEvent


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def content_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def append_audit(
    session: Session,
    event_type: str,
    actor: str,
    payload: dict[str, Any],
    prospect_id: str | None = None,
) -> AuditEvent:
    previous = session.scalar(
        select(AuditEvent)
        .where(AuditEvent.prospect_id == prospect_id)
        .order_by(desc(AuditEvent.created_at), desc(AuditEvent.id))
        .limit(1)
    )
    previous_hash = previous.content_hash if previous else "0" * 64
    created_at = datetime.now(UTC)
    digest = content_hash(
        {
            "prospect_id": prospect_id,
            "event_type": event_type,
            "actor": actor,
            "payload": payload,
            "previous_hash": previous_hash,
            "created_at": created_at.isoformat(),
        }
    )
    event = AuditEvent(
        prospect_id=prospect_id,
        event_type=event_type,
        actor=actor,
        payload=payload,
        previous_hash=previous_hash,
        content_hash=digest,
        created_at=created_at,
    )
    session.add(event)
    return event


def verify_audit_chain(events: list[AuditEvent]) -> bool:
    previous_hash = "0" * 64
    for event in sorted(events, key=lambda item: (item.created_at, item.id)):
        if event.previous_hash != previous_hash:
            return False
        previous_hash = event.content_hash
    return True

