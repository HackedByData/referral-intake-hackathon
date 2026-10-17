from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.db import get_session
from app.schemas import AuditEventView
from app.workflow import audit

router = APIRouter(prefix="/api", tags=["audit"])


@router.get("/audit", response_model=list[AuditEventView])
def list_audit(referral_id: str | None = None, session: Session = Depends(get_session)):
    return audit.list_events(session, referral_id=referral_id)
