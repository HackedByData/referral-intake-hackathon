"""Propose lessons when a coordinator's call differs from the AI's. Lessons stay `proposed` until staff approve."""
import logging

from sqlmodel import Session

from app.beds.models import Bed, Site
from app.knowledge import store
from app.knowledge.profile import build_profile
from app.rules.criteria import Criteria
from app.schemas import AnalysisResult, CoordinatorDecision, CountySuggestion

log = logging.getLogger(__name__)
AGREES = {"approve": "approve", "deny": "deny", "request_info": "needs_review"}  # coordinator action -> AI decision


def _first_sentence(text: str) -> str:
    return text.split(". ")[0].rstrip(".")[:300]


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
                  chosen_bed_id: str | None, held_bed_id: str | None, criteria: Criteria) -> list[int]:
    """Create proposed lessons for this decision. Call after the decision has been committed."""
    profile = build_profile(result, criteria.rules)
    ai = result.recommendation.decision
    ids: list[int] = []
    if AGREES[decision.decision] != ai:
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
        ids.append(store.create(
            session, "rule_interpretation", profile,
            {"rule_id": o.rule_id, "ai_status": ai_result.status, "coordinator_status": o.status,
             "ai_reasoning": _first_sentence(ai_result.reasoning)},
            o.note, decision.coordinator, referral_id).id)
    if decision.decision == "approve" and chosen_bed_id and held_bed_id and chosen_bed_id != held_bed_id:
        held, chosen = _bed_side(session, held_bed_id), _bed_side(session, chosen_bed_id)
        if held and chosen:
            ids.append(store.create(session, "bed_preference", profile, {"held": held, "chosen": chosen},
                                    decision.notes, decision.coordinator, referral_id).id)
    return ids


def _norm_county(c: str | None) -> str:
    return (c or "").strip().lower().removesuffix(" county")


def from_county(session: Session, referral_id: str, result: AnalysisResult, old: CountySuggestion | None,
                new_county: str, coordinator: str, criteria: Criteria) -> int | None:
    plan = (result.summary.health_plan or "").strip()
    old_county = old.county if old else None
    if not plan or _norm_county(old_county) == _norm_county(new_county):
        return None
    return store.create(
        session, "county_mapping", build_profile(result, criteria.rules),
        {"health_plan": plan, "ai_county": old_county, "coordinator_county": new_county.strip()},
        "", coordinator, referral_id).id
