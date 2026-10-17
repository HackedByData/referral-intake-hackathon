"""Kipu EHR integration: MOCKED. Event rules forbid connecting to IHH systems.

The real Kipu API field mapping has NOT been done; the draft below carries only what we know, under our own names.
Swap MockKipuClient for a real client that implements KipuClient after the event.
"""
from datetime import datetime
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel

from app.schemas import BedOption


class KipuAdmissionDraft(BaseModel):
    mock: bool = True
    note: str = "Prototype payload. Not sent to Kipu; field mapping to the real Kipu API is not done."
    referral_id: str
    bed: BedOption
    admitting_diagnosis: str | None
    summary: str
    ai_recommendation: str
    rule_outcomes: dict[str, int]
    approved_by: str
    approved_at: datetime


class KipuClient(Protocol):
    def create_admission(self, draft: KipuAdmissionDraft) -> str: ...


class MockKipuClient:
    def __init__(self, outbox_dir: Path):
        self.folder = outbox_dir / "kipu"

    def create_admission(self, draft: KipuAdmissionDraft) -> str:
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder / f"{draft.referral_id}.json").write_text(draft.model_dump_json(indent=2), "utf-8")
        return f"MOCK-KIPU-{draft.referral_id}"
