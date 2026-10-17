from datetime import datetime

import pytest
from sqlmodel import Session, SQLModel

import app.beds.models  # noqa: F401
import app.workflow.models  # noqa: F401
from app.beds.models import Bed, Site
from app.db import make_engine
from app.rules.criteria import load_criteria
from app.schemas import AnalysisResult, RuleResult
from app.workflow.models import Referral
from app.workflow.pipeline import Pipeline, mark_interrupted
from tests.factories import make_packet, make_summary


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    eng = make_engine("sqlite://")
    SQLModel.metadata.create_all(eng)
    with Session(eng) as s:
        s.add(Site(id="sa", name="Demo Site A"))
        s.add(Bed(id="M1", site_id="sa", room="101", label="A", sex_designation="male"))
        now = datetime(2026, 10, 17)
        for rid in ("R1", "R2"):
            s.add(Referral(id=rid, filename=f"{rid}.pdf", pdf_path=str(tmp_path / f"{rid}.pdf"),
                           created_at=now, updated_at=now))
        s.commit()
    return eng


async def fake_transcribe(llm, pdf_path, cache_root, concurrency=8, on_progress=None):
    if on_progress:
        on_progress(3, 3)
    return make_packet()


def all_pass_evaluate(results_status="pass"):
    async def evaluate(llm, packet, criteria, concurrency=8):
        results = [RuleResult(rule_id=r.id, status="not_applicable", reasoning="n/a", confidence="high")
                   for r in criteria.rules]
        for r in results:
            if r.rule_id in ("GEN-1",):
                r.status, r.evidence = results_status, []
        return make_summary(), results, {"summary": 0.1, "rules": 0.2}
    return evaluate


def pipeline(engine, evaluate=None, transcribe=fake_transcribe):
    return Pipeline(llm=object(), criteria=load_criteria(), engine_getter=lambda: engine,
                    transcribe=transcribe, evaluate=evaluate or all_pass_evaluate())


async def test_run_success_stores_result_and_holds_bed(engine):
    await pipeline(engine).run("R1")
    with Session(engine) as s:
        ref = s.get(Referral, "R1")
        assert ref.status == "ready_for_review" and ref.packet_hash == "testhash"
        result = AnalysisResult.model_validate_json(ref.result_json)
        assert result.recommendation.held_bed.bed_id == "M1"
        from app.beds.service import held_bed_id
        assert held_bed_id(s, "R1") == "M1"


async def test_second_referral_cannot_take_held_bed(engine):
    await pipeline(engine).run("R1")
    await pipeline(engine).run("R2")
    with Session(engine) as s:
        r2 = AnalysisResult.model_validate_json(s.get(Referral, "R2").result_json)
        assert r2.recommendation.held_bed is None
        assert any(r.kind == "no_bed" for r in r2.recommendation.review_reasons)


async def test_failure_marks_failed_with_message(engine):
    async def boom(*a, **k):
        raise RuntimeError("vision call failed")
    await pipeline(engine, transcribe=boom).run("R1")
    with Session(engine) as s:
        ref = s.get(Referral, "R1")
        assert ref.status == "failed" and "vision call failed" in ref.error


def test_mark_interrupted(engine):
    with Session(engine) as s:
        ref = s.get(Referral, "R1")
        ref.status = "transcribing"
        other = s.get(Referral, "R2")
        other.status = "ready_for_review"  # fixture default "uploaded" is also in flight
        s.add(ref)
        s.add(other)
        s.commit()
        assert mark_interrupted(s) == 1
        ref = s.get(Referral, "R1")
        assert ref.status == "failed" and "interrupted" in ref.error
