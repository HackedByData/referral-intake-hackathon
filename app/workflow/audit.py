"""Append-only audit log. Records AI recommendations, coordinator decisions and overrides (our QC trail)."""
import json
from datetime import datetime, timezone

from sqlmodel import Session, select

from app.schemas import AuditEventView
from app.workflow.models import AuditEvent


def record(session: Session, action: str, actor: str = "system", referral_id: str | None = None,
           **detail) -> AuditEvent:
    ev = AuditEvent(ts=datetime.now(timezone.utc).replace(tzinfo=None), actor=actor, referral_id=referral_id,
                    action=action, detail_json=json.dumps(detail, default=str))
    session.add(ev)
    session.commit()
    session.refresh(ev)
    return ev


def list_events(session: Session, referral_id: str | None = None) -> list[AuditEventView]:
    q = select(AuditEvent).order_by(AuditEvent.id)
    if referral_id:
        q = q.where(AuditEvent.referral_id == referral_id)
    return [AuditEventView(id=e.id, ts=e.ts, actor=e.actor, referral_id=e.referral_id, action=e.action,
                           detail=json.loads(e.detail_json)) for e in session.exec(q).all()]
