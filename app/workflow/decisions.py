"""Apply a coordinator's final decision: transactional bed assignment, drafts, mock Kipu, audit."""
import logging
from collections import Counter
from datetime import datetime, timezone

from sqlmodel import Session

from app.beds import service as beds
from app.config import get_settings
from app.knowledge import capture
from app.rules.criteria import Criteria
from app.schemas import AnalysisResult, CoordinatorDecision, CoordinatorDecisionRecord, Drafts
from app.workflow import audit
from app.workflow.drafts import denial_draft, info_request_draft
from app.workflow.kipu import KipuAdmissionDraft, KipuClient, MockKipuClient
from app.workflow.models import Referral

log = logging.getLogger(__name__)
DECIDABLE = ("ready_for_review", "info_requested")
AGREES = {"approve": "approve", "deny": "deny", "request_info": "needs_review"}


class DecisionError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


def get_kipu() -> KipuClient:
    return MockKipuClient(get_settings().outbox_dir)


def apply_decision(session: Session, referral_id: str, decision: CoordinatorDecision, criteria: Criteria,
                   kipu: KipuClient) -> Referral:
    ref = session.get(Referral, referral_id)
    if ref is None:
        raise DecisionError(404, f"Referral {referral_id} not found")
    if ref.status not in DECIDABLE or not ref.result_json:
        raise DecisionError(409, f"Referral {referral_id} cannot be decided in status '{ref.status}'")
    result = AnalysisResult.model_validate_json(ref.result_json)
    ai = result.recommendation.decision
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    drafts = Drafts.model_validate_json(ref.drafts_json) if ref.drafts_json else Drafts()
    by_override = {o.rule_id: o.status for o in decision.rule_overrides}
    effective = [r.model_copy(update={"status": by_override[r.rule_id]}) if r.rule_id in by_override else r
                 for r in result.rule_results]  # the stored AnalysisResult is left untouched
    overridden_fail = frozenset(i for i, st in by_override.items() if st == "fail")
    chosen_bed_id: str | None = None
    held_before: str | None = None

    if decision.decision == "approve":
        held_before = beds.held_bed_id(session, referral_id)  # read before assign deactivates the hold
        bed_id = decision.bed_id or held_before
        if bed_id is None:
            raise DecisionError(409, "No bed is held for this referral (it may have expired or the AI "
                                     "recommended deny). Choose a bed from bed-matches and pass bed_id. "
                                     "If the patient's county isn't confirmed yet, confirm it first.")
        try:
            assignment = beds.assign(session, bed_id, referral_id, client_label=referral_id)
        except (beds.BedUnavailable, beds.BedNotFound) as e:
            raise DecisionError(409, f"Bed {bed_id} is no longer available. Choose another bed. ({e})")
        assignment_id = assignment.id
        chosen_bed_id = bed_id
        try:  # assign has committed; compensate if anything below fails
            bed = beds.bed_option(session, session.get(beds.Bed, bed_id))
            draft = KipuAdmissionDraft(
                referral_id=referral_id, bed=bed, admitting_diagnosis=result.summary.admitting_diagnosis,
                summary=result.summary.one_liner, ai_recommendation=ai,
                rule_outcomes=dict(Counter(r.status for r in effective)),
                approved_by=decision.coordinator, approved_at=now)
            drafts.kipu_payload = draft.model_dump(mode="json")
            drafts.kipu_reference = kipu.create_admission(draft)
            ref.status = "approved"
            record = _finish(session, ref, decision, ai, drafts, now)
        except Exception as e:
            log.exception("Approval of %s failed after bed assignment; compensating", referral_id)
            session.rollback()
            beds.discharge(session, assignment_id)
            try:
                beds.hold(session, bed_id, referral_id, get_settings().hold_ttl_hours)
            except Exception:
                log.exception("Could not re-hold bed %s for %s after rollback", bed_id, referral_id)
            raise DecisionError(500, f"Approval failed and was rolled back: {e}")
    elif decision.decision == "deny":
        drafts.denial = denial_draft(referral_id, result.recommendation, effective, criteria.by_id(),
                                     decision.coordinator, decision.notes, overridden_fail)
        beds.release_hold(session, referral_id)
        ref.status = "denied"
        record = _finish(session, ref, decision, ai, drafts, now)
    else:
        drafts.info_request = info_request_draft(referral_id, result.recommendation, decision.coordinator,
                                                 decision.notes)
        ref.status = "info_requested"
        record = _finish(session, ref, decision, ai, drafts, now)
    audit.record(session, "coordinator_decision", actor=decision.coordinator, referral_id=referral_id,
                 decision=decision.decision, ai_recommendation=ai, agreed_with_ai=record.agreed_with_ai,
                 overrides=[o.model_dump() for o in decision.rule_overrides], notes=decision.notes)
    try:  # lessons are proposed only after the decision is safely committed; never break the decision
        capture.from_decision(session, referral_id, result, decision, chosen_bed_id, held_before, criteria,
                             record.agreed_with_ai)
    except Exception:
        log.exception("Lesson capture failed for %s; decision unaffected", referral_id)
        session.rollback()
    session.refresh(ref)
    return ref


def _finish(session: Session, ref: Referral, decision: CoordinatorDecision, ai: str, drafts: Drafts,
            now: datetime) -> CoordinatorDecisionRecord:
    record = CoordinatorDecisionRecord(**decision.model_dump(), decided_at=now, ai_recommendation=ai,
                                       agreed_with_ai=AGREES[decision.decision] == ai)
    ref.decision_json, ref.drafts_json, ref.updated_at = record.model_dump_json(), drafts.model_dump_json(), now
    session.add(ref)
    session.commit()
    return record
