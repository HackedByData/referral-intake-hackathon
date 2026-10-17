import json
from datetime import datetime, timedelta

import pytest
from sqlmodel import Session, SQLModel, select

import app.beds.models  # noqa: F401
import app.workflow.models  # noqa: F401
from app.beds import service
from app.beds.models import Bed, Hold, Site
from app.db import make_engine
from app.rules.criteria import load_criteria
from app.schemas import AnalysisResult, CoordinatorDecision, Drafts, RuleOverride
from app.workflow.decisions import DecisionError, apply_decision
from app.workflow.kipu import MockKipuClient
from app.workflow.models import Referral
from tests.factories import make_analysis, make_bed_option, make_result


@pytest.fixture
def setup(tmp_path):
    eng = make_engine("sqlite://")
    SQLModel.metadata.create_all(eng)
    s = Session(eng)
    s.add(Site(id="sa", name="Demo Site A"))
    s.add_all([Bed(id="M1", site_id="sa", room="101", label="A", sex_designation="male"),
               Bed(id="M2", site_id="sa", room="102", label="A", sex_designation="male")])
    now = datetime(2026, 10, 17)
    analysis = make_analysis("approve", held_bed=make_bed_option("M1"))
    s.add(Referral(id="R1", filename="a.pdf", pdf_path="x", status="ready_for_review", created_at=now,
                   updated_at=now, result_json=analysis.model_dump_json()))
    s.commit()
    service.hold(s, "M1", "R1", ttl_hours=4)
    return s, MockKipuClient(tmp_path)


def approve(**kw):
    return CoordinatorDecision(decision="approve", coordinator="Coordinator A", **kw)


def test_approve_assigns_held_bed_and_writes_kipu(setup, tmp_path):
    s, kipu = setup
    ref = apply_decision(s, "R1", approve(), load_criteria(), kipu)
    assert ref.status == "approved"
    assert s.get(Bed, "M1").status == "occupied"
    drafts = Drafts.model_validate_json(ref.drafts_json)
    assert drafts.kipu_reference == "MOCK-KIPU-R1" and (tmp_path / "kipu" / "R1.json").exists()
    assert json.loads(ref.decision_json)["agreed_with_ai"] is True


def test_double_submit_is_rejected_without_second_assignment(setup):
    s, kipu = setup
    apply_decision(s, "R1", approve(), load_criteria(), kipu)
    with pytest.raises(DecisionError) as e:
        apply_decision(s, "R1", approve(bed_id="M2"), load_criteria(), kipu)
    assert e.value.status_code == 409
    assert s.get(Bed, "M2").status == "available"


def test_expired_hold_taken_by_other_referral_gives_409_and_keeps_status(setup):
    s, kipu = setup
    h = s.exec(select(Hold)).first()
    h.expires_at = service.utcnow() - timedelta(minutes=1)
    s.add(h)
    s.commit()
    service.hold(s, "M1", "R9", ttl_hours=4)
    with pytest.raises(DecisionError) as e:
        apply_decision(s, "R1", approve(), load_criteria(), kipu)
    assert e.value.status_code == 409 and "No bed is held for this referral" in str(e.value)
    assert s.get(Referral, "R1").status == "ready_for_review"


def test_approve_other_bed_by_id(setup):
    s, kipu = setup
    apply_decision(s, "R1", approve(bed_id="M2"), load_criteria(), kipu)
    assert s.get(Bed, "M2").status == "occupied"


def test_deny_releases_hold_and_drafts_denial_and_records_disagreement(setup):
    s, kipu = setup
    ref = apply_decision(s, "R1", CoordinatorDecision(decision="deny", coordinator="Coordinator A"),
                         load_criteria(), kipu)
    assert ref.status == "denied" and service.held_bed_id(s, "R1") is None
    assert Drafts.model_validate_json(ref.drafts_json).denial.startswith("DRAFT")
    assert json.loads(ref.decision_json)["agreed_with_ai"] is False


def test_request_info_keeps_hold(setup):
    s, kipu = setup
    ref = apply_decision(s, "R1", CoordinatorDecision(decision="request_info", coordinator="C"),
                         load_criteria(), kipu)
    assert ref.status == "info_requested" and service.held_bed_id(s, "R1") == "M1"
    assert Drafts.model_validate_json(ref.drafts_json).info_request.startswith("DRAFT")


def test_unknown_referral_404(setup):
    s, kipu = setup
    with pytest.raises(DecisionError) as e:
        apply_decision(s, "NOPE", approve(), load_criteria(), kipu)
    assert e.value.status_code == 404


class ExplodingKipu:
    def create_admission(self, draft):
        raise OSError("disk full")


def test_approve_failure_compensates_and_can_be_retried(setup, tmp_path):
    from app.beds.models import Assignment
    s, kipu = setup
    with pytest.raises(DecisionError) as e:
        apply_decision(s, "R1", approve(), load_criteria(), ExplodingKipu())
    assert e.value.status_code == 500 and "rolled back" in str(e.value)
    assert service.held_bed_id(s, "R1") == "M1"
    assert s.get(Referral, "R1").status == "ready_for_review"
    assert [a for a in s.exec(select(Assignment)).all() if a.status == "active"] == []
    ref = apply_decision(s, "R1", approve(), load_criteria(), kipu)
    assert ref.status == "approved"


def _set_results(s, results):
    ref = s.get(Referral, "R1")
    a = AnalysisResult.model_validate_json(ref.result_json)
    ref.result_json = a.model_copy(update={"rule_results": results}).model_dump_json()
    s.add(ref)
    s.commit()


def _denial(s, kipu, overrides=()):
    ref = apply_decision(s, "R1", CoordinatorDecision(decision="deny", coordinator="C",
                         rule_overrides=[RuleOverride(rule_id=i, status=st, note="n") for i, st in overrides]),
                         load_criteria(), kipu)
    return Drafts.model_validate_json(ref.drafts_json).denial


def test_denial_does_not_cite_unverified_or_gen_fails(setup):
    s, kipu = setup
    _set_results(s, [make_result("MOB-3", "fail", verified=False), make_result("GEN-2", "fail"),
                     make_result("MH-2", "fail")])
    text = _denial(s, kipu)
    assert "MH-2" in text and "MOB-3" not in text and "GEN-2" not in text


def test_denial_skips_rule_overridden_to_pass(setup):
    s, kipu = setup
    _set_results(s, [make_result("MOB-3", "fail"), make_result("MH-2", "fail")])
    text = _denial(s, kipu, [("MH-2", "pass")])
    assert "MOB-3" in text and "MH-2" not in text


def test_denial_cites_rule_overridden_to_fail_even_unverified(setup):
    s, kipu = setup
    _set_results(s, [make_result("MOB-3", "pass")])
    text = _denial(s, kipu, [("MOB-3", "fail")])
    assert "MOB-3" in text


def test_kipu_rule_outcomes_reflect_overrides_and_stored_result_untouched(setup):
    s, kipu = setup
    _set_results(s, [make_result("MOB-3", "fail"), make_result("MH-2", "pass")])
    ref = apply_decision(s, "R1", approve(rule_overrides=[RuleOverride(rule_id="MOB-3", status="pass", note="n")]),
                         load_criteria(), kipu)
    payload = Drafts.model_validate_json(ref.drafts_json).kipu_payload
    assert payload["rule_outcomes"] == {"pass": 2}
    stored = AnalysisResult.model_validate_json(ref.result_json)
    assert [r.status for r in stored.rule_results] == ["fail", "pass"]


def test_no_hold_no_bed_id_message(setup):
    s, kipu = setup
    service.release_hold(s, "R1")
    with pytest.raises(DecisionError) as e:
        apply_decision(s, "R1", approve(), load_criteria(), kipu)
    assert e.value.status_code == 409 and "No bed is held for this referral" in str(e.value)
