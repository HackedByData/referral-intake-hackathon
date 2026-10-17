"""Lesson table. Holds de-identified profiles, rule IDs and coordinator notes only: no packet quotes, no names."""
from pydantic import NaiveDatetime
from sqlmodel import Field, SQLModel


class Lesson(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    kind: str = Field(index=True)  # LessonKind
    status: str = Field(default="proposed", index=True)  # LessonStatus
    created_at: NaiveDatetime
    created_by: str
    source_referral_id: str | None = Field(default=None, index=True)
    profile_json: str = "{}"
    payload_json: str = "{}"
    note: str = ""
    needs_note: bool = False
    reviewed_by: str | None = None
    reviewed_at: NaiveDatetime | None = None
    times_applied: int = 0
    last_applied_at: NaiveDatetime | None = None
