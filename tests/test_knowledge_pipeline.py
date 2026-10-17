from datetime import datetime

import pytest
from sqlmodel import Session, SQLModel

import app.beds.models  # noqa: F401
import app.knowledge.models  # noqa: F401
import app.workflow.models  # noqa: F401
from app.beds.models import Bed, Site
from app.db import make_engine
from app.knowledge import store
from app.rules.criteria import load_criteria
from app.schemas import AnalysisResult, Evidence, RuleResult
from app.workflow.models import Referral
from app.workflow.pipeline import Pipeline
from tests.factories import PAGE_1, PAGE_2, PAGE_3, make_packet, make_summary
from tests.kn_helpers import PROFILE, approved, bed_payload


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    eng = make_engine("sqlite://")
    SQLModel.metadata.create_all(eng)
    with Session(eng) as s:
        for sid in ("sa", "sb"):
            s.add(Site(id=sid, name=f"Facility {sid}", county="Orange", program_type="recuperative_care"))
        s.add_all([Bed(id="M1", site_id="sa", room="101", label="A", sex_designation="male"),
                   Bed(id="M2", site_id="sb", room="101", label="A", sex_designation="male")])
        now = datetime(2026, 10, 17)
        s.add(Referral(id="R1", filename="R1.pdf", pdf_path=str(tmp_path / "R1.pdf"), created_at=now, updated_at=now))
        s.commit()
    return eng


async def fake_transcribe(llm, pdf_path, cache_root, concurrency=8, on_progress=None):
    return make_packet([PAGE_1, PAGE_2, PAGE_3, "AUTHORIZATION\nHealth plan: CalOptima Medi-Cal"])


def make_pipeline(engine, plan="CalOptima", seen=None):
    async def summarize(llm, packet, criteria):
        s = make_summary(health_plan=plan, health_plan_evidence=[Evidence(page=4, quote=f"Health plan: {plan}")])
        return s.model_copy(update={"bed_needs": s.bed_needs.model_copy(update={"county": None})})  # model never fills it

    async def evaluate_rules(llm, packet, criteria, concurrency=8, guidance=None, on_group_done=None):
        if on_group_done:
            on_group_done(1, 1)
        if seen is not None:
            seen.append(guidance)
        out = [RuleResult(rule_id=r.id, status="not_applicable", reasoning="n/a", confidence="high")
               for r in criteria.rules]
        for r in out:
            if r.rule_id == "GEN-1":
                r.status = "pass"
        return out
    return Pipeline(llm=object(), criteria=load_criteria(), engine_getter=lambda: engine, transcribe=fake_transcribe,
                    summarize=summarize, evaluate_rules=evaluate_rules)


def load(engine) -> AnalysisResult:
    with Session(engine) as s:
        return AnalysisResult.model_validate_json(s.get(Referral, "R1").result_json)


async def test_no_lessons_means_clear_knowledge_step(engine):
    seen = []
    await make_pipeline(engine, seen=seen).run("R1")
    r = load(engine)
    assert r.recommendation.held_bed.bed_id == "M1" and r.knowledge_used == []
    assert seen == [{}] or seen == [None]
    step = [t for t in r.recommendation.tree if t.node == "knowledge"][0]
    assert step.outcome == "clear" and step.explanation == "No approved lessons applied."
    assert r.profile is not None and r.profile.county == "Orange" and r.recommendation.precedents == []


async def test_approved_lessons_are_used_and_counted(engine):
    seen = []
    with Session(engine) as s:
        bed = approved(s, "bed_preference", bed_payload("sa", "sb"))
        rule = approved(s, "rule_interpretation", {"rule_id": "MOB-3", "ai_status": "pass", "coordinator_status": "fail"})
        prec = approved(s, "decision_precedent", {"ai_decision": "approve", "coordinator_decision": "deny"})
        pending = store.create(s, "bed_preference", PROFILE, bed_payload("sb", "sa"), "n", "c", "R1")
        ids = (bed.id, rule.id, prec.id, pending.id)
    await make_pipeline(engine, seen=seen).run("R1")
    r = load(engine)
    assert r.recommendation.held_bed.bed_id == "M2"  # re-ranked to the preferred facility
    assert any("lesson #" in n for n in r.recommendation.held_bed.match_notes)
    assert r.knowledge_used == sorted(ids[:3])
    assert "Lesson #" in seen[0]["MOB-3"][0]
    step = [t for t in r.recommendation.tree if t.node == "knowledge"][0]
    assert step.outcome == "triggered" and all(f"#{i}" in step.explanation for i in ids[:3])
    assert [p.lesson_id for p in r.recommendation.precedents] == [prec.id]
    with Session(engine) as s:
        counts = {v.id: v.times_applied for v in store.list_lessons(s)}
    assert counts == {ids[0]: 1, ids[1]: 1, ids[2]: 1, ids[3]: 0}


async def test_learned_county_mapping_is_used_and_recorded(engine):
    with Session(engine) as s:
        lesson = approved(s, "county_mapping", {"health_plan": "Zeta Care", "ai_county": None,
                                                "coordinator_county": "Orange"})
        lid = lesson.id
    await make_pipeline(engine, plan="Zeta Care Medi-Cal").run("R1")
    r = load(engine)
    assert r.summary.suggested_county.county == "Orange"
    assert f"learned from lesson #{lid}" in r.summary.suggested_county.basis
    assert lid in r.knowledge_used and r.recommendation.held_bed is not None
