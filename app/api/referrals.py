"""Referral intake HTTP routes."""
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response, UploadFile
from sqlmodel import Session, select

from app.beds import service as beds
from app.config import get_settings
from app.db import get_session
from app.ingest.ocr import CORRECTION
from app.knowledge import apply as knowledge
from app.knowledge import capture
from app.knowledge import store as knowledge_store
from app.knowledge.profile import build_profile
from app.ingest.render import InvalidPDF, page_count, render_page, validate_pdf
from app.rules.criteria import load_criteria
from app.rules.tree import decide as decide_tree
from app.schemas import (
    AnalysisResult, BedOption, CoordinatorDecision, CoordinatorDecisionRecord, CountyConfirm, CountySuggestion, Drafts, PageInfo,
    PageMarkdown, ReferralDetails, ReferralListItem, ReferralView, ReviewReason, RuleResultView,
)
from app.workflow import audit
from app.workflow.decisions import DecisionError, apply_decision, get_kipu
from app.workflow.models import Referral
from app.workflow.pipeline import Pipeline, get_pipeline

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/referrals", tags=["referrals"])


def _get(session: Session, referral_id: str) -> Referral:
    ref = session.get(Referral, referral_id)
    if ref is None:
        raise HTTPException(404, f"Referral {referral_id} not found")
    return ref


def _result(ref: Referral) -> AnalysisResult:
    if not ref.result_json:
        raise HTTPException(409, f"Referral {ref.id} has not been analyzed yet (status '{ref.status}')")
    return AnalysisResult.model_validate_json(ref.result_json)


def _list_item(ref: Referral) -> dict:
    rec = AnalysisResult.model_validate_json(ref.result_json).recommendation if ref.result_json else None
    return dict(id=ref.id, filename=ref.filename, status=ref.status, created_at=ref.created_at,
                decision=rec.decision if rec else None, headline=rec.headline if rec else None)


def _view(ref: Referral) -> ReferralView:
    result = AnalysisResult.model_validate_json(ref.result_json) if ref.result_json else None
    return ReferralView(
        **_list_item(ref), progress=ref.progress, error=ref.error,
        one_liner=result.summary.one_liner if result else None,
        recommendation=result.recommendation if result else None,
        coordinator_decision=CoordinatorDecisionRecord.model_validate_json(ref.decision_json) if ref.decision_json else None,
        county=result.summary.suggested_county if result else None)


@router.post("", response_model=ReferralView, status_code=202)
async def upload(file: UploadFile, background: BackgroundTasks, session: Session = Depends(get_session),
                 pipeline: Pipeline = Depends(get_pipeline)):
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise HTTPException(503, "ANTHROPIC_API_KEY is not configured on the server")
    referral_id = f"R-{uuid.uuid4().hex[:6].upper()}"
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    path = settings.uploads_dir / f"{referral_id}.pdf"
    path.write_bytes(await file.read())
    invalid: str | None = None
    try:
        validate_pdf(path)
    except InvalidPDF as e:
        invalid = str(e)
    if invalid is not None:  # unlink outside the except block: on Windows the traceback can pin the file handle
        path.unlink(missing_ok=True)
        raise HTTPException(400, invalid)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    ref = Referral(id=referral_id, filename=file.filename or "packet.pdf", pdf_path=str(path),
                   created_at=now, updated_at=now)
    session.add(ref)
    session.commit()
    audit.record(session, "uploaded", referral_id=referral_id, filename=ref.filename)
    background.add_task(pipeline.run, referral_id)
    session.refresh(ref)
    return _view(ref)


@router.get("", response_model=list[ReferralListItem])
def list_referrals(session: Session = Depends(get_session)):
    refs = session.exec(select(Referral).order_by(Referral.created_at.desc())).all()
    return [ReferralListItem(**_list_item(r)) for r in refs]


@router.get("/{referral_id}", response_model=ReferralView)
def get_referral(referral_id: str, session: Session = Depends(get_session)):
    return _view(_get(session, referral_id))


@router.get("/{referral_id}/details", response_model=ReferralDetails)
def get_details(referral_id: str, session: Session = Depends(get_session)):
    ref = _get(session, referral_id)
    result = _result(ref)
    rules = load_criteria().by_id()
    views = []
    for r in result.rule_results:
        rule = rules.get(r.rule_id)
        if rule is None:
            continue
        views.append(RuleResultView(**r.model_dump(), kind=rule.kind, section=rule.section, source=rule.source,
                                    text=rule.text, superseded_by=rule.superseded_by, human_review=rule.human_review))
    pages = [PageInfo(page=p.page, doc_type=p.doc_type, orientation=p.orientation, legibility=p.legibility)
             for p in result.packet.pages]
    return ReferralDetails(id=ref.id, status=ref.status, prompt_version=result.prompt_version, model=result.model,
                           summary=result.summary, rule_results=views, recommendation=result.recommendation,
                           pages=pages, timings=result.timings)


@router.get("/{referral_id}/pages/{page}", response_model=PageMarkdown)
def get_page(referral_id: str, page: int, session: Session = Depends(get_session)):
    result = _result(_get(session, referral_id))
    for p in result.packet.pages:
        if p.page == page:
            return p
    raise HTTPException(404, f"Page {page} not found")


@router.get("/{referral_id}/pages/{page}/image")
def get_page_image(referral_id: str, page: int, session: Session = Depends(get_session)):
    ref = _get(session, referral_id)
    pdf = get_settings().uploads_dir / f"{ref.id}.pdf"
    if page < 1 or not pdf.exists():
        raise HTTPException(404, f"Page {page} not found")
    rotate = 0
    if ref.result_json:
        pages = {p.page: p for p in AnalysisResult.model_validate_json(ref.result_json).packet.pages}
        if page not in pages:
            raise HTTPException(404, f"Page {page} not found")
        rotate = CORRECTION.get(pages[page].orientation, 0)
    elif page > page_count(pdf):
        raise HTTPException(404, f"Page {page} not found")
    data, media = render_page(pdf, page, rotate=rotate)
    return Response(content=data, media_type=media)


@router.get("/{referral_id}/bed-matches", response_model=list[BedOption])
def bed_matches(referral_id: str, session: Session = Depends(get_session)):
    result = _result(_get(session, referral_id))
    return beds.find_matches(session, result.summary.bed_needs, limit=10, for_referral=referral_id)


@router.post("/{referral_id}/decision", response_model=ReferralView)
def decide(referral_id: str, decision: CoordinatorDecision, session: Session = Depends(get_session)):
    try:
        ref = apply_decision(session, referral_id, decision, load_criteria(), get_kipu())
    except DecisionError as e:
        raise HTTPException(e.status_code, str(e))
    return _view(ref)


@router.post("/{referral_id}/county", response_model=ReferralView)
def confirm_county(referral_id: str, body: CountyConfirm, session: Session = Depends(get_session)):
    """Coordinator confirms or overrides the county; re-runs matching (no AI) and re-holds."""
    ref = _get(session, referral_id)
    if ref.status not in ("ready_for_review", "info_requested") or not ref.result_json:
        raise HTTPException(409, f"County cannot be changed in status '{ref.status}'")
    county = body.county.strip()
    if not county:
        raise HTTPException(422, "county must not be empty")
    result = AnalysisResult.model_validate_json(ref.result_json)
    old = result.summary.suggested_county or CountySuggestion()
    marker = "; AI suggestion: "
    ai_basis = old.basis or ""
    if old.confirmed:  # keep the original AI basis; never nest earlier "Confirmed by ..." text
        ai_basis = ai_basis.split(marker, 1)[1] if marker in ai_basis else ""
    basis = f"Confirmed by {body.coordinator}" + (f"; AI suggestion: {ai_basis}" if ai_basis else "")
    summary = result.summary.model_copy(update={
        "suggested_county": old.model_copy(update={"county": county, "confirmed": True,
                                                   "confirmed_by": body.coordinator, "basis": basis}),
        "bed_needs": result.summary.bed_needs.model_copy(update={"county": county})})
    beds.release_hold(session, referral_id)
    criteria = load_criteria()
    options = beds.find_matches(session, summary.bed_needs, limit=10, for_referral=referral_id)
    used = list(result.knowledge_used)  # lessons already used by the analysis (guidance, county) still apply
    profile = build_profile(result.model_copy(update={"summary": summary}), criteria.rules)
    precedent_list = []
    try:
        options, bed_ids = knowledge.rerank_beds(session, options, profile)
        precedent_list = knowledge.precedents(session, profile)
        used += bed_ids + [p.lesson_id for p in precedent_list]
    except Exception:
        log.exception("Knowledge re-rank failed for %s; using default bed order", referral_id)
    rec = decide_tree(criteria.rules, result.rule_results, summary, options[:3])
    rec = knowledge.attach(rec, precedent_list, used)
    if rec.held_bed:
        try:
            beds.hold(session, rec.held_bed.bed_id, referral_id, get_settings().hold_ttl_hours)
        except (beds.BedUnavailable, beds.BedNotFound):
            rec = rec.model_copy(update={"held_bed": None, "review_reasons": rec.review_reasons + [
                ReviewReason(kind="no_bed", rule_ids=[], detail="Matched bed was taken before it could be held.")]})
    result = result.model_copy(update={"summary": summary, "recommendation": rec, "profile": profile,
                                       "knowledge_used": sorted(set(used))})
    ref.result_json = result.model_dump_json()
    ref.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    session.add(ref)
    session.commit()
    audit.record(session, "county_confirmed", actor=body.coordinator, referral_id=referral_id, county=county,
                 held_bed=rec.held_bed.bed_id if rec.held_bed else None)
    try:
        knowledge_store.mark_applied(session, result.knowledge_used)
    except Exception:
        log.exception("Could not record lesson usage for %s", referral_id)
        session.rollback()
    try:  # propose a county_mapping lesson when the coordinator disagrees with the AI's suggestion
        capture.from_county(session, referral_id, result, old, county, body.coordinator, load_criteria())
    except Exception:
        log.exception("County lesson capture failed for %s", referral_id)
        session.rollback()
    session.refresh(ref)
    return _view(ref)


@router.get("/{referral_id}/drafts", response_model=Drafts)
def get_drafts(referral_id: str, session: Session = Depends(get_session)):
    ref = _get(session, referral_id)
    return Drafts.model_validate_json(ref.drafts_json) if ref.drafts_json else Drafts()


@router.post("/{referral_id}/retry", response_model=ReferralView, status_code=202)
def retry(referral_id: str, background: BackgroundTasks, session: Session = Depends(get_session),
          pipeline: Pipeline = Depends(get_pipeline)):
    ref = _get(session, referral_id)
    if ref.status != "failed":
        raise HTTPException(409, f"Only failed referrals can be retried (status '{ref.status}')")
    ref.status, ref.error = "uploaded", None
    session.add(ref)
    session.commit()
    audit.record(session, "retry", referral_id=referral_id)
    background.add_task(pipeline.run, referral_id)
    session.refresh(ref)
    return _view(ref)
