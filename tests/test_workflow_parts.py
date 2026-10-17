import json

import pytest
from sqlmodel import Session, SQLModel

import app.workflow.models  # noqa: F401
from app.db import make_engine
from app.workflow import audit
from app.workflow.drafts import denial_draft, info_request_draft
from app.workflow.kipu import KipuAdmissionDraft, MockKipuClient
from app.schemas import HospitalQuestion, Recommendation
from tests.factories import make_bed_option, make_result, make_rule


@pytest.fixture
def session():
    engine = make_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def rec(decision="deny", questions=()):
    return Recommendation(decision=decision, headline="h", top_reasons=[], review_reasons=[],
                          hospital_questions=list(questions), tree=[])


def test_audit_record_and_list(session):
    audit.record(session, "uploaded", referral_id="R1", filename="a.pdf")
    audit.record(session, "other", referral_id="R2")
    events = audit.list_events(session, referral_id="R1")
    assert len(events) == 1 and events[0].detail == {"filename": "a.pdf"} and events[0].actor == "system"
    assert len(audit.list_events(session)) == 2


def test_denial_draft_cites_failed_rules_and_is_marked_draft():
    rules = {"MH-2": make_rule("MH-2", "exclude"), "MOB-3": make_rule("MOB-3")}
    results = [make_result("MH-2", "fail"), make_result("MOB-3", "pass")]
    text = denial_draft("R1", rec(), results, rules, "Coordinator A", "Call back if plan changes.")
    assert text.startswith("DRAFT")
    assert "MH-2" in text and "MOB-3" not in text
    assert "Coordinator A" in text and "Call back if plan changes." in text


def test_info_request_draft_lists_questions():
    r = rec("needs_review", [HospitalQuestion(rule_id="INF-1", question="Can you send the MRSA swab result?")])
    text = info_request_draft("R1", r, "Coordinator A", "")
    assert text.startswith("DRAFT") and "Can you send the MRSA swab result?" in text and "INF-1" in text


def test_mock_kipu_writes_outbox(tmp_path):
    client = MockKipuClient(tmp_path)
    draft = KipuAdmissionDraft(referral_id="R1", bed=make_bed_option(), admitting_diagnosis="cellulitis",
                               summary="Adult man...", ai_recommendation="approve", rule_outcomes={"pass": 3},
                               approved_by="Coordinator A", approved_at="2026-10-17T12:00:00")
    ref = client.create_admission(draft)
    assert ref == "MOCK-KIPU-R1"
    data = json.loads((tmp_path / "kipu" / "R1.json").read_text("utf-8"))
    assert data["mock"] is True and data["bed"]["bed_id"] == "SA-101-A"
