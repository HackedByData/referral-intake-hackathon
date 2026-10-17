"""Progress/time-remaining estimate and the decision confidence summary."""
import json
from datetime import datetime

from sqlmodel import Session, SQLModel

import app.workflow.models  # noqa: F401
from app.rules.tree import decide
from app.schemas import Conflict, Evidence
from app.workflow.progress import Estimates, estimate, new_state
from tests.factories import make_bed_option, make_result, make_rule, make_summary

EST = Estimates(sec_per_page=4.0, summary_sec=40.0, rules_sec=45.0)


def state(**kw):
    s = new_state(EST, now=1000.0)
    s.update(kw)
    return s


def test_reading_uses_live_page_rate_plus_later_phases():
    p = estimate(state(pages_done=10, pages_total=30), now=1020.0)
    assert p["eta_seconds"] == 40 + 40 + 45  # 2 s/page measured x 20 pages left, then summary + rules
    assert p["detail"] == "Reading page 10 of 30" and 1 <= p["percent"] <= 99


def test_countdown_keeps_moving_between_events():
    s = state(phase="summary", phase_t0=1030.0)
    a, b = estimate(s, now=1040.0), estimate(s, now=1060.0)
    assert b["eta_seconds"] < a["eta_seconds"] and b["percent"] > a["percent"]


def test_rule_phase_counts_down_from_history_not_group_extrapolation():
    s = state(phase="rules", phase_t0=1070.0, groups_done=2, groups_total=8)
    p = estimate(s, now=1080.0)
    assert p["eta_seconds"] == 35 and "2 of 8" in p["detail"]


def test_estimate_stays_in_bounds_when_running_over():
    p = estimate(state(phase="rules", phase_t0=1000.0), now=5000.0)
    assert p["eta_seconds"] >= 3 and 1 <= p["percent"] <= 99


def test_referral_view_computes_progress_at_read_time():
    from app.api.referrals import _view
    from app.db import make_engine
    from app.workflow.models import Referral
    eng = make_engine("sqlite://")
    SQLModel.metadata.create_all(eng)
    with Session(eng) as s:
        st = new_state(EST)
        st.update(phase="summary", stage="analyzing")
        ref = Referral(id="R1", filename="a.pdf", pdf_path="x", status="analyzing", created_at=datetime(2026, 10, 17),
                       updated_at=datetime(2026, 10, 17), progress="Summarizing the packet",
                       progress_json=json.dumps(st))
        s.add(ref)
        s.commit()
        v = _view(s.get(Referral, "R1"))
    assert 1 <= v.progress_percent <= 99 and v.eta_seconds > 40


RULES = [make_rule("GEN-1"), make_rule("GEN-2"), make_rule("GEN-3"), make_rule("MOB-3"),
         make_rule("MH-2", "exclude"), make_rule("DOC-6", "document", source="checklist")]


def results(**status):
    base = {"GEN-1": "pass", "GEN-2": "fail", "GEN-3": "fail", "MOB-3": "pass", "MH-2": "pass", "DOC-6": "pass"}
    base.update({k.replace("_", "-"): v for k, v in status.items()})
    return [make_result(k, v) for k, v in base.items()]


def test_confidence_is_reported_with_level_factors_and_note():
    rec = decide(RULES, results(), make_summary(), [make_bed_option()])
    c = rec.confidence
    assert c is not None and 0 <= c.score <= 100 and c.level in ("high", "medium", "low")
    assert c.factors and "not a calibrated probability" in c.note


def test_missing_facts_and_unverified_quotes_lower_confidence():
    clean = decide(RULES, results(), make_summary(), [make_bed_option()]).confidence.score
    res = results(DOC_6="unknown")
    res[3] = make_result("MOB-3", "fail", verified=False)
    messy = decide(RULES, res, make_summary(), [make_bed_option()]).confidence.score
    assert messy < clean


def test_conflicts_lower_confidence():
    conflict = Conflict(topic="Walking", statements=[Evidence(page=1, quote="a"), Evidence(page=2, quote="b")],
                        related_rule_ids=["DOC-6"], why_it_matters="x")
    base = decide(RULES, results(DOC_6="unknown"), make_summary(), [make_bed_option()]).confidence.score
    with_conflict = decide(RULES, results(DOC_6="unknown"), make_summary(conflicts=[conflict]),
                           [make_bed_option()]).confidence.score
    assert with_conflict < base


def test_deny_confidence_rests_on_the_deciding_rules():
    rec = decide(RULES, results(MH_2="fail"), make_summary(), [])
    assert rec.decision == "deny" and rec.confidence.level == "high"
    assert any("MH-2" in f for f in rec.confidence.factors)
