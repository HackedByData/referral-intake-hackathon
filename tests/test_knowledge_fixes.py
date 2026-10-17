"""Fix round 1: dedupe, county attribution, re-match carry-over, ordering, ambiguous plans, None-vs-empty lists."""
import pytest
from sqlmodel import Session

from app.knowledge import store
from app.knowledge.apply import similarity
from app.knowledge.profile import build_profile, from_summary
from app.rules.criteria import load_criteria
from app.schemas import AnalysisResult, CaseProfile, CountySuggestion, LessonReview, RuleOverride
from app.workflow.decisions import DecisionError
from app.workflow.models import Referral
from tests.factories import make_analysis, make_summary
from tests.kn_helpers import approved, session  # noqa: F401
from tests.test_knowledge_capture import client, decide, kinds, setup  # noqa: F401
from tests.test_knowledge_pipeline import engine, load, make_pipeline  # noqa: F401

OVR = [RuleOverride(rule_id="MOB-3", status="fail", note="Uses a cane")]


# ---------- 1. duplicates ----------
def test_repeat_request_info_creates_no_duplicate_lessons(setup):
    s, kipu = setup
    decide(s, kipu, decision="request_info", rule_overrides=OVR)
    first = kinds(s)
    assert first == ["decision_precedent", "rule_interpretation"]
    decide(s, kipu, decision="request_info", rule_overrides=OVR)
    assert kinds(s) == first


def test_request_info_then_approve_same_override_keeps_one_rule_interpretation(setup):
    s, kipu = setup
    decide(s, kipu, decision="request_info", rule_overrides=OVR)
    decide(s, kipu, decision="approve", rule_overrides=OVR)
    assert kinds(s).count("rule_interpretation") == 1


def test_rejected_lesson_does_not_block_a_new_capture(setup):
    s, kipu = setup
    decide(s, kipu, decision="request_info", rule_overrides=OVR)
    for l in store.list_lessons(s):
        store.review(s, l.id, "reject", LessonReview(reviewer="staff"))
    decide(s, kipu, decision="approve", rule_overrides=OVR)
    assert kinds(s).count("rule_interpretation") == 2


# ---------- (a) payload, (c) compensation ----------
def test_lesson_payload_has_no_ai_reasoning(setup):
    s, kipu = setup
    decide(s, kipu, decision="approve", rule_overrides=OVR)
    assert "ai_reasoning" not in store.list_lessons(s)[0].payload


def test_compensation_path_creates_no_lessons(setup):
    s, _ = setup

    class Exploding:
        def create_admission(self, draft):
            raise RuntimeError("kipu down")
    with pytest.raises(DecisionError) as e:
        decide(s, Exploding(), decision="approve", bed_id="M2", rule_overrides=OVR)
    assert e.value.status_code == 500
    assert kinds(s) == []


# ---------- 2. county attribution ----------
def county_payloads(engine):
    with Session(engine) as s:
        return [l.payload for l in store.list_lessons(s, kind="county_mapping")]


def test_county_reconfirm_keeps_ai_county_attribution(client):
    c, engine = client
    c.post("/api/referrals/R1/county", json={"county": "Riverside", "coordinator": "Coord"})
    out = c.post("/api/referrals/R1/county", json={"county": "Fresno", "coordinator": "Coord"}).json()
    assert out["county"]["ai_county"] == "Orange"
    assert county_payloads(engine) == [
        {"health_plan": "CalOptima", "ai_county": "Orange", "coordinator_county": "Riverside"},
        {"health_plan": "CalOptima", "ai_county": "Orange", "coordinator_county": "Fresno"}]


def test_county_back_to_ai_value_creates_no_lesson(client):
    c, engine = client
    c.post("/api/referrals/R1/county", json={"county": "Riverside", "coordinator": "Coord"})
    c.post("/api/referrals/R1/county", json={"county": "orange", "coordinator": "Coord"})
    assert len(county_payloads(engine)) == 1


async def test_pipeline_records_ai_county(engine):
    await make_pipeline(engine).run("R1")
    assert load(engine).summary.suggested_county.ai_county == "Orange"


# ---------- 5. ambiguous built-in plans ----------
@pytest.mark.parametrize("plan,expect", [("IEHP", 0), ("Molina Healthcare", 0), ("Zeta Unknown Plan", 1)])
def test_ambiguous_builtin_plans_are_not_auto_resolved(session, plan, expect):
    from app.knowledge import capture
    a = make_analysis("approve").model_copy(update={"summary": make_summary(health_plan=plan)})
    capture.from_county(session, "R1", a, CountySuggestion(county=None), "Kern", "Coord", load_criteria())
    assert len(store.list_lessons(session)) == expect


# ---------- 3. re-match carry-over ----------
def test_rematch_keeps_only_valid_inherited_lessons_and_counts_only_new(client):
    c, engine = client
    other = CaseProfile(county="Fresno", payer="Other", sex="female", wheelchair=True, oxygen=True, isolation=True)
    with Session(engine) as s:
        rule = approved(s, "rule_interpretation",
                        {"rule_id": "MOB-3", "ai_status": "pass", "coordinator_status": "fail"}).id
        stale = approved(s, "bed_preference", {"held": {"facility_id": "a"}, "chosen": {"facility_id": "b"}},
                         profile=other).id
        cty = approved(s, "county_mapping", {"health_plan": "CalOptima", "ai_county": None,
                                             "coordinator_county": "Orange"}).id
        ref = s.get(Referral, "R1")
        r = AnalysisResult.model_validate_json(ref.result_json).model_copy(
            update={"knowledge_used": [rule, stale, cty]})
        ref.result_json = r.model_dump_json()
        s.add(ref)
        s.commit()

    def stored():
        with Session(engine) as s:
            return AnalysisResult.model_validate_json(s.get(Referral, "R1").result_json)

    def applied():
        with Session(engine) as s:
            return {v.id: v.times_applied for v in store.list_lessons(s)}
    out = c.post("/api/referrals/R1/county", json={"county": "Orange", "coordinator": "Coord"}).json()
    assert stored().knowledge_used == sorted([rule, cty])  # stale bed lesson dropped, county lesson still matches
    assert applied()[rule] == 0 and applied()[cty] == 0  # inherited ids are not re-counted
    step = [t for t in out["recommendation"]["tree"] if t["node"] == "knowledge"][0]
    assert f"#{rule}" in step["explanation"] and f"#{stale}" not in step["explanation"]
    c.post("/api/referrals/R1/county", json={"county": "Riverside", "coordinator": "Coord"})
    assert stored().knowledge_used == [rule]  # confirmed county no longer equals the learned one


# ---------- 4. ordering ----------
async def test_guidance_profile_includes_the_county(engine, monkeypatch):
    from app.workflow import pipeline as pl
    seen = []
    real = pl.knowledge.rule_guidance

    def spy(session, rules, profile):
        seen.append(profile)
        return real(session, rules, profile)
    monkeypatch.setattr(pl.knowledge, "rule_guidance", spy)
    await make_pipeline(engine).run("R1")
    assert seen[0].county == "Orange" and seen[0].payer == "CalOptima"


# ---------- 6. None vs empty lists ----------
def test_similarity_empty_lists_are_real_and_none_is_unknown():
    clean = CaseProfile(county="Orange", payer="A", systems=[], flagged_rule_ids=[])
    flagged = CaseProfile(county="Orange", payer="A", systems=[], flagged_rule_ids=["MOB-3"])
    assert similarity(clean, clean) == 1.0
    assert similarity(clean, flagged) < 1.0
    assert similarity(CaseProfile(county="Orange", payer="A"), flagged) == 1.0


def test_clean_case_far_from_flagged_precedent_is_below_threshold():
    clean = CaseProfile(county="Orange", payer="CalOptima", sex="male", wheelchair=False, oxygen=False,
                        isolation=False, systems=[], flagged_rule_ids=[])
    precedent = CaseProfile(county="Fresno", payer="Other", sex="male", wheelchair=False, oxygen=False,
                            isolation=False, systems=["GI"], flagged_rule_ids=["MOB-3"])
    assert similarity(clean, precedent) < 0.5


def test_build_profile_gives_real_lists_and_from_summary_gives_none():
    p = build_profile(make_analysis(), [])
    assert p.systems == [] and p.flagged_rule_ids == []
    q = from_summary(make_summary())
    assert q.systems is None and q.flagged_rule_ids is None
