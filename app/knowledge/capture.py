"""Propose lessons when a coordinator's call differs from the AI's. Lessons stay `proposed` until staff approve."""
import json
import logging

from sqlmodel import Session, select

from app.beds.models import Bed, Site
from app.knowledge import store
from app.knowledge.models import Lesson
from app.knowledge.profile import build_profile
from app.rules.county import builtin_plan_counties
from app.rules.criteria import Criteria
from app.schemas import AnalysisResult, CoordinatorDecision, CountySuggestion

log = logging.getLogger(__name__)


def _exists(session: Session, referral_id: str, kind: str, match) -> bool:
    """True if a non-rejected lesson of this kind from this referral already matches (repeat decisions)."""
    rows = session.exec(select(Lesson).where(Lesson.source_referral_id == referral_id, Lesson.kind == kind,
                                             Lesson.status != "rejected")).all()
    return any(match(json.loads(r.payload_json)) for r in rows)


def _bed_side(session: Session, bed_id: str) -> dict | None:
    bed = session.get(Bed, bed_id)
    if bed is None:
        return None
    site = session.get(Site, bed.site_id)
    return {"facility_id": bed.site_id, "facility": site.name if site else bed.site_id,
            "county": site.county if site else None, "sex_designation": bed.sex_designation,
            "wheelchair_accessible": bed.wheelchair_accessible, "oxygen_ok": bed.oxygen_ok,
            "isolation_ok": bed.isolation_ok, "ground_floor": bed.ground_floor}


def from_decision(session: Session, referral_id: str, result: AnalysisResult, decision: CoordinatorDecision,
                  chosen_bed_id: str | None, held_bed_id: str | None, criteria: Criteria,
                  agreed_with_ai: bool) -> list[int]:
    """Create proposed lessons for this decision. Call after the decision has been committed."""
    profile = build_profile(result, criteria.rules)
    ai = result.recommendation.decision
    ids: list[int] = []
    if not agreed_with_ai and not _exists(
            session, referral_id, "decision_precedent", lambda p: p.get("coordinator_decision") == decision.decision):
        flagged = sorted({i for s in result.recommendation.tree if s.outcome == "triggered" for i in s.rule_ids})
        ids.append(store.create(
            session, "decision_precedent", profile,
            {"ai_decision": ai, "coordinator_decision": decision.decision, "ai_rule_ids": flagged},
            decision.notes, decision.coordinator, referral_id).id)
    ai_status = {r.rule_id: r for r in result.rule_results}
    for o in decision.rule_overrides:
        ai_result = ai_status.get(o.rule_id)
        if ai_result is None or ai_result.status == o.status:
            continue
        if _exists(session, referral_id, "rule_interpretation",
                   lambda p: p.get("rule_id") == o.rule_id and p.get("coordinator_status") == o.status):
            continue
        ids.append(store.create(
            session, "rule_interpretation", profile,
            {"rule_id": o.rule_id, "ai_status": ai_result.status, "coordinator_status": o.status},
            o.note, decision.coordinator, referral_id).id)
    if decision.decision == "approve" and chosen_bed_id and held_bed_id and chosen_bed_id != held_bed_id:
        held, chosen = _bed_side(session, held_bed_id), _bed_side(session, chosen_bed_id)
        if held and chosen and not _exists(
                session, referral_id, "bed_preference",
                lambda p: p.get("chosen", {}).get("facility_id") == chosen["facility_id"]):
            ids.append(store.create(session, "bed_preference", profile, {"held": held, "chosen": chosen},
                                    decision.notes, decision.coordinator, referral_id).id)
    return ids


def effective_ai_county(old: CountySuggestion | None) -> str | None:
    """The AI's county for this referral, whatever the coordinator has confirmed since."""
    if old is None:
        return None
    if old.ai_county is not None:
        return old.ai_county
    return None if old.confirmed else old.county


def _norm_county(c: str | None) -> str:
    return (c or "").strip().lower().removesuffix(" county")


def from_county(session: Session, referral_id: str, result: AnalysisResult, old: CountySuggestion | None,
                new_county: str, coordinator: str, criteria: Criteria) -> int | None:
    plan = (result.summary.health_plan or "").strip()
    ai_county = effective_ai_county(old)
    if not plan or _norm_county(ai_county) == _norm_county(new_county):
        return None
    builtin = builtin_plan_counties(plan)
    if builtin is not None and len(builtin) != 1:  # deliberately ambiguous plan (several counties or none): don't auto-resolve
        log.info("Not proposing a county mapping for ambiguous built-in plan on %s", referral_id)
        return None
    if _exists(session, referral_id, "county_mapping",
               lambda p: _norm_county(p.get("coordinator_county")) == _norm_county(new_county)):
        return None
    return store.create(
        session, "county_mapping", build_profile(result, criteria.rules),
        {"health_plan": plan, "ai_county": ai_county, "coordinator_county": new_county.strip()},
        "", coordinator, referral_id).id

