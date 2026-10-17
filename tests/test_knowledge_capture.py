from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel

import app.beds.models  # noqa: F401
import app.knowledge.models  # noqa: F401
import app.workflow.models  # noqa: F401
from app.beds import service
from app.beds.models import Bed, Site
from app.db import make_engine, set_engine
from app.knowledge import store
from app.rules.criteria import load_criteria
from app.schemas import CoordinatorDecision, RuleOverride
from app.workflow.decisions import DecisionError, apply_decision
from app.workflow.kipu import MockKipuClient
from app.workflow.models import Referral
from tests.factories import make_analysis, make_bed_option, make_summary


@pytest.fixture
def setup(tmp_path):
    eng = make_engine("sqlite://")
    SQLModel.metadata.create_all(eng)
    s = Session(eng)
    s.add(Site(id="sa", name="Demo Site A", county="Orange", program_type="recuperative_care"))
    s.add(Site(id="sb", name="Demo Site B", county="Orange", program_type="recuperative_care"))
    s.add_all([Bed(id="M1", site_id="sa", room="101", label="A", sex_designation="male"),
               Bed(id="M2", site_id="sb", room="102", label="A", sex_designation="male")])
    now = datetime(2026, 10, 17)
    analysis = make_analysis("approve", held_bed=make_bed_option("M1"))
    analysis = analysis.model_copy(update={"summary": make_summary(health_plan="CalOptima")})
    s.add(Referral(id="R1", filename="a.pdf", pdf_path="x", status="ready_for_review", created_at=now,
                   updated_at=now, result_json=analysis.model_dump_json()))
    s.commit()
    service.hold(s, "M1", "R1", ttl_hours=4)
    return s, MockKipuClient(tmp_path)


def decide(s, kipu, **kw):
    kw.setdefault("coordinator", "Coordinator A")
    return apply_decision(s, "R1", CoordinatorDecision(**kw), load_criteria(), kipu)


def kinds(s):
    return sorted(l.kind for l in store.list_lessons(s))


def test_deny_when_ai_approved_creates_decision_precedent(setup):
    s, kipu = setup
    decide(s, kipu, decision="deny", notes="Needs a higher level of care")
    [lesson] = store.list_lessons(s)
    assert lesson.kind == "decision_precedent" and lesson.status == "proposed"
    assert lesson.payload["ai_decision"] == "approve" and lesson.payload["coordinator_decision"] == "deny"
    assert lesson.note == "Needs a higher level of care" and lesson.source_referral_id == "R1"
    assert lesson.profile.county == "Orange" and lesson.profile.payer == "CalOptima"


def test_override_status_change_creates_rule_interpretation(setup):
    s, kipu = setup
    decide(s, kipu, decision="approve", rule_overrides=[RuleOverride(rule_id="MOB-3", status="fail", note="Uses a cane")])
    [lesson] = store.list_lessons(s)
    assert lesson.kind == "rule_interpretation" and lesson.note == "Uses a cane"
    assert lesson.payload["rule_id"] == "MOB-3"
    assert lesson.payload["ai_status"] == "pass" and lesson.payload["coordinator_status"] == "fail"


def test_override_with_same_status_creates_nothing(setup):
    s, kipu = setup
    decide(s, kipu, decision="approve", rule_overrides=[RuleOverride(rule_id="MOB-3", status="pass", note="same")])
    assert kinds(s) == []


def test_approve_with_different_bed_creates_bed_preference(setup):
    s, kipu = setup
    decide(s, kipu, decision="approve", bed_id="M2", notes="Closer to the clinic")
    [lesson] = store.list_lessons(s)
    assert lesson.kind == "bed_preference"
    assert lesson.payload["held"]["facility_id"] == "sa" and lesson.payload["chosen"]["facility_id"] == "sb"
    assert lesson.payload["chosen"]["facility"] == "Demo Site B"


def test_agreeing_approve_creates_no_lessons(setup):
    s, kipu = setup
    decide(s, kipu, decision="approve")
    assert kinds(s) == []


def test_double_submit_creates_no_duplicate_lessons(setup):
    s, kipu = setup
    decide(s, kipu, decision="deny")
    with pytest.raises(DecisionError) as e:
        decide(s, kipu, decision="deny")
    assert e.value.status_code == 409
    assert kinds(s) == ["decision_precedent"]


def test_capture_failure_never_breaks_the_decision(setup, monkeypatch):
    s, kipu = setup
    from app.knowledge import capture

    def boom(*a, **k):
        raise RuntimeError("capture exploded")
    monkeypatch.setattr(capture, "from_decision", boom)
    ref = decide(s, kipu, decision="deny")
    assert ref.status == "denied"


# ---------- county capture via the API ----------
@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SEED_MODE", "demo")
    engine = make_engine("sqlite://")
    set_engine(engine)
    from app.main import app
    with TestClient(app) as c:
        with Session(engine) as s:
            from app.schemas import CountySuggestion
            a = make_analysis("approve")
            summary = make_summary(health_plan="CalOptima",
                                   suggested_county=CountySuggestion(county="Orange", candidates=["Orange"], basis="x"))
            a = a.model_copy(update={"summary": summary})
            s.add(Referral(id="R1", filename="a.pdf", pdf_path="x", status="ready_for_review",
                           created_at=datetime(2026, 10, 17), updated_at=datetime(2026, 10, 17),
                           result_json=a.model_dump_json()))
            s.commit()
        yield c, engine


def test_county_change_creates_county_mapping_lesson(client):
    c, engine = client
    assert c.post("/api/referrals/R1/county", json={"county": "Riverside", "coordinator": "Coord"}).status_code == 200
    with Session(engine) as s:
        [lesson] = store.list_lessons(s)
    assert lesson.kind == "county_mapping" and lesson.payload == {
        "health_plan": "CalOptima", "ai_county": "Orange", "coordinator_county": "Riverside"}


def test_county_confirm_same_as_ai_creates_nothing(client):
    c, engine = client
    c.post("/api/referrals/R1/county", json={"county": "orange county", "coordinator": "Coord"})
    with Session(engine) as s:
        assert store.list_lessons(s) == []
