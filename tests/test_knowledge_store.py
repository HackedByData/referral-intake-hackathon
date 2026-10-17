import pytest
from fastapi.testclient import TestClient

from app.knowledge import store
from app.knowledge.apply import similarity
from app.knowledge.profile import build_profile
from app.schemas import CaseProfile, LessonReview
from tests.factories import make_analysis, make_result, make_rule, make_summary
from tests.kn_helpers import PROFILE, session  # noqa: F401


def test_build_profile_from_result():
    rules = [make_rule("SYS-1", kind="eligible", section="Integumentary (eligible)", source="by_system"),
             make_rule("SYS-2", kind="eligible", section="GI (eligible)", source="by_system"),
             make_rule("MOB-3"), make_rule("DOC-1", kind="document"), make_rule("NOTE-1", kind="note")]
    results = [make_result("SYS-1", "pass"), make_result("SYS-2", "unknown"), make_result("MOB-3", "fail"),
               make_result("DOC-1", "unknown"), make_result("NOTE-1", "unknown")]
    a = make_analysis().model_copy(update={"rule_results": results})
    a = a.model_copy(update={"summary": make_summary(health_plan="CalOptima")})
    p = build_profile(a, rules)
    assert p.county == "Orange" and p.payer == "CalOptima" and p.sex == "male" and p.mobility_aid == "walker"
    assert p.systems == ["Integumentary"]
    assert p.flagged_rule_ids == ["DOC-1", "MOB-3"]


def test_similarity_identical_disjoint_and_none():
    assert similarity(PROFILE, PROFILE) == 1.0
    other = CaseProfile(county="Fresno", payer="Other", sex="female", wheelchair=True, oxygen=True, isolation=True,
                        systems=["GI"], flagged_rule_ids=["X-1"])
    assert similarity(PROFILE, other) < 0.3
    sparse = CaseProfile(county="Orange")  # None / empty fields are not held against the match
    assert similarity(PROFILE, sparse) == 1.0
    assert similarity(PROFILE, CaseProfile(county="Fresno")) == 0.0


def test_create_is_proposed_and_flags_missing_note(session):
    l = store.create(session, "decision_precedent", PROFILE, {"ai_decision": "approve"}, "  ", "coord", "R1")
    assert l.status == "proposed" and l.needs_note is True
    l2 = store.create(session, "decision_precedent", PROFILE, {}, "has note", "coord", "R1")
    assert l2.needs_note is False


def test_review_transitions_and_edited_note(session):
    l = store.create(session, "rule_interpretation", PROFILE, {}, "", "coord", "R1")
    v = store.review(session, l.id, "approve", LessonReview(reviewer="staff", note="Edited note"))
    assert v.status == "approved" and v.note == "Edited note" and v.needs_note is False
    assert v.reviewed_by == "staff" and v.reviewed_at is not None
    with pytest.raises(store.LessonError) as e:
        store.review(session, l.id, "reject", LessonReview(reviewer="staff"))
    assert e.value.status_code == 409
    assert store.review(session, l.id, "retire", LessonReview(reviewer="staff")).status == "retired"
    with pytest.raises(store.LessonError):
        store.review(session, l.id, "approve", LessonReview(reviewer="staff"))
    l2 = store.create(session, "rule_interpretation", PROFILE, {}, "n", "coord", "R1")
    with pytest.raises(store.LessonError):
        store.review(session, l2.id, "retire", LessonReview(reviewer="staff"))
    assert store.review(session, l2.id, "reject", LessonReview(reviewer="staff")).status == "rejected"
    with pytest.raises(store.LessonError) as e:
        store.review(session, 999, "approve", LessonReview(reviewer="staff"))
    assert e.value.status_code == 404
    from app.workflow import audit
    actions = [ev.action for ev in audit.list_events(session)]
    assert actions.count("lesson_approved") == 1 and "lesson_rejected" in actions and "lesson_retired" in actions


def test_list_filters_approved_and_mark_applied(session):
    a = store.create(session, "bed_preference", PROFILE, {}, "n", "c", "R1")
    store.create(session, "county_mapping", PROFILE, {}, "n", "c", "R1")
    store.review(session, a.id, "approve", LessonReview(reviewer="s"))
    assert len(store.list_lessons(session)) == 2
    assert [v.id for v in store.list_lessons(session, status="approved")] == [a.id]
    assert len(store.list_lessons(session, kind="county_mapping")) == 1
    assert [l.id for l in store.approved(session, "bed_preference")] == [a.id]
    store.mark_applied(session, [a.id])
    assert store.list_lessons(session, status="approved")[0].times_applied == 1


# ---------- API ----------
@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SEED_MODE", "none")
    from app.db import make_engine, set_engine
    engine = make_engine("sqlite://")
    set_engine(engine)
    from app.main import app
    with TestClient(app) as c:
        yield c, engine


def test_api_review_flow_and_409(client):
    c, engine = client
    from sqlmodel import Session
    with Session(engine) as s:
        l = store.create(s, "decision_precedent", PROFILE, {"ai_decision": "approve", "coordinator_decision": "deny"},
                         "", "coord", "R1")
        lid = l.id
    assert c.get("/api/knowledge", params={"status": "proposed"}).json()[0]["id"] == lid
    r = c.post(f"/api/knowledge/{lid}/approve", json={"reviewer": "Staff", "note": "Fine"})
    assert r.status_code == 200 and r.json()["status"] == "approved" and r.json()["needs_note"] is False
    assert c.post(f"/api/knowledge/{lid}/approve", json={"reviewer": "Staff"}).status_code == 409
    assert c.post(f"/api/knowledge/{lid}/reject", json={"reviewer": "Staff"}).status_code == 409
    assert c.post("/api/knowledge/999/approve", json={"reviewer": "Staff"}).status_code == 404
    assert c.post(f"/api/knowledge/{lid}/retire", json={"reviewer": "Staff"}).json()["status"] == "retired"
    exp = c.get("/api/knowledge/export").json()
    assert exp["count"] == 1 and exp["lessons"][0]["id"] == lid
