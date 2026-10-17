"""Referral and audit tables. JSON columns hold schemas.AnalysisResult / CoordinatorDecisionRecord / Drafts."""
from pydantic import NaiveDatetime
from sqlmodel import Field, SQLModel


class Referral(SQLModel, table=True):
    id: str = Field(primary_key=True)
    filename: str
    pdf_path: str
    packet_hash: str | None = None
    status: str = "uploaded"
    progress: str | None = None
    error: str | None = None
    created_at: NaiveDatetime
    updated_at: NaiveDatetime
    result_json: str | None = None
    decision_json: str | None = None
    drafts_json: str | None = None


class AuditEvent(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    ts: NaiveDatetime
    actor: str
    referral_id: str | None = Field(default=None, index=True)
    action: str
    detail_json: str = "{}"
