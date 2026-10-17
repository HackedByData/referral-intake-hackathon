"""Orchestrates one referral: transcribe -> summarize -> county -> evaluate (+ approved-lesson guidance) -> verify -> decide -> hold bed. Runs as a background task."""
import logging
import time
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from sqlmodel import Session, select

from app.beds import service as beds
from app.config import Settings, get_settings
from app.db import get_engine
from app.ingest.ocr import transcribe_packet
from app.llm import StructuredLLM
from app.knowledge import apply as knowledge
from app.knowledge import store as knowledge_store
from app.knowledge.profile import build_profile, from_summary
from app.rules.county import learned_lesson_id, load_plan_map, merge_plan_map, suggest_county
from app.rules.criteria import Criteria, load_criteria
from app.rules.evaluate import evaluate_rules, summarize
from app.rules.prompts import PROMPT_VERSION
from app.rules.tree import decide
from app.rules.verify import verify_results, verify_summary
from app.schemas import AnalysisResult, ReviewReason
from app.workflow import audit
from app.workflow.models import Referral

log = logging.getLogger(__name__)
IN_FLIGHT = ("uploaded", "transcribing", "analyzing")


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Pipeline:
    def __init__(self, llm, criteria: Criteria, engine_getter=get_engine, settings: Settings | None = None,
                 transcribe=transcribe_packet, summarize=summarize, evaluate_rules=evaluate_rules):
        self.llm = llm
        self.criteria = criteria
        self.engine_getter = engine_getter
        self.settings = settings or get_settings()
        self.transcribe = transcribe
        self.summarize = summarize
        self.evaluate_rules = evaluate_rules

    def _update(self, referral_id: str, **fields) -> None:
        with Session(self.engine_getter()) as s:
            ref = s.get(Referral, referral_id)
            for k, v in fields.items():
                setattr(ref, k, v)
            ref.updated_at = _now()
            s.add(ref)
            s.commit()

    async def run(self, referral_id: str) -> None:
        t0 = time.perf_counter()
        try:
            with Session(self.engine_getter()) as s:
                pdf_path = Path(s.get(Referral, referral_id).pdf_path)
            self._update(referral_id, status="transcribing", progress="Transcribing pages", error=None)
            packet = await self.transcribe(
                self.llm, pdf_path, self.settings.cache_dir, concurrency=self.settings.ocr_concurrency,
                on_progress=lambda d, n: self._update(referral_id, progress=f"Transcribing page {d} of {n}"))
            t1 = time.perf_counter()
            self._update(referral_id, status="analyzing", packet_hash=packet.packet_hash,
                         progress="Checking admission criteria")
            t_sum = time.perf_counter()
            summary = await self.summarize(self.llm, packet, self.criteria)
            summary = verify_summary(summary, packet)
            t_rules = time.perf_counter()
            used: list[int] = []
            additions, learned = {}, {}
            try:  # approved lessons only; a knowledge failure must never block the analysis
                with Session(self.engine_getter()) as s:
                    additions, learned = knowledge.plan_map_additions(s)
            except Exception:
                log.exception("Knowledge lookup failed for %s; continuing without it", referral_id)
            plan_map = merge_plan_map(load_plan_map(), additions)
            suggestion = suggest_county(summary, plan_map, learned)  # the model only extracts; code maps plan -> county
            suggestion = suggestion.model_copy(update={"ai_county": suggestion.county})
            lesson_id = learned_lesson_id(summary, plan_map, learned)
            if lesson_id is not None and suggestion.county:
                used.append(lesson_id)
            summary = summary.model_copy(update={
                "suggested_county": suggestion,
                "bed_needs": summary.bed_needs.model_copy(update={"county": suggestion.county})})
            guidance: dict[str, list[str]] = {}
            try:  # the guidance profile includes the county, so it is built after the county suggestion
                with Session(self.engine_getter()) as s:
                    evaluated = [r for rs in self.criteria.groups().values() for r in rs]
                    guidance, gids = knowledge.rule_guidance(s, evaluated, from_summary(summary))
                    used += gids
            except Exception:
                log.exception("Rule guidance lookup failed for %s; continuing without it", referral_id)
            results = await self.evaluate_rules(self.llm, packet, self.criteria,
                                                concurrency=self.settings.rules_concurrency, guidance=guidance)
            results = verify_results(results, packet)
            timings = {"summary": t_rules - t_sum, "rules": time.perf_counter() - t_rules}
            profile = build_profile(
                AnalysisResult.model_construct(summary=summary, rule_results=results), self.criteria.rules)
            with Session(self.engine_getter()) as s:
                options = beds.find_matches(s, summary.bed_needs, limit=10, for_referral=referral_id)
                precedent_list = []
                try:
                    options, bed_ids = knowledge.rerank_beds(s, options, profile)
                    precedent_list = knowledge.precedents(s, profile)
                    used += bed_ids + [p.lesson_id for p in precedent_list]
                except Exception:
                    log.exception("Knowledge re-rank failed for %s; using default bed order", referral_id)
                rec = decide(self.criteria.rules, results, summary, options[:3])
                rec = knowledge.attach(rec, precedent_list, used)
                if rec.held_bed:
                    try:
                        beds.hold(s, rec.held_bed.bed_id, referral_id, self.settings.hold_ttl_hours)
                    except (beds.BedUnavailable, beds.BedNotFound):
                        log.warning("Bed %s taken between match and hold", rec.held_bed.bed_id)
                        rec = rec.model_copy(update={"held_bed": None, "review_reasons": rec.review_reasons + [
                            ReviewReason(kind="no_bed", rule_ids=[],
                                         detail="Matched bed was taken before it could be held.")]})
                result = AnalysisResult(
                    prompt_version=PROMPT_VERSION, model=getattr(self.llm, "model", "unknown"), packet=packet,
                    summary=summary, rule_results=results, recommendation=rec, profile=profile,
                    knowledge_used=sorted(set(used)),
                    timings={"transcribe": t1 - t0, **timings, "total": time.perf_counter() - t0})
                ref = s.get(Referral, referral_id)
                ref.result_json, ref.status, ref.progress, ref.updated_at = (
                    result.model_dump_json(), "ready_for_review", None, _now())
                s.add(ref)
                s.commit()
                try:
                    knowledge_store.mark_applied(s, result.knowledge_used)
                except Exception:
                    log.exception("Could not record lesson usage for %s", referral_id)
                    s.rollback()
                audit.record(s, "analysis_complete", referral_id=referral_id, ai_recommendation=rec.decision,
                             held_bed=rec.held_bed.bed_id if rec.held_bed else None,
                             knowledge_used=result.knowledge_used, seconds=round(time.perf_counter() - t0, 1))
        except Exception as e:
            log.exception("Pipeline failed for %s", referral_id)
            self._update(referral_id, status="failed", progress=None, error=f"{type(e).__name__}: {e}")
            with Session(self.engine_getter()) as s:
                audit.record(s, "analysis_failed", referral_id=referral_id, error=str(e))


def mark_interrupted(session: Session) -> int:
    """On startup: anything still in flight was interrupted by a restart."""
    stuck = session.exec(select(Referral).where(Referral.status.in_(IN_FLIGHT))).all()
    for ref in stuck:
        ref.status, ref.progress, ref.error, ref.updated_at = (
            "failed", None, "Processing was interrupted (server restart). Use retry.", _now())
        session.add(ref)
    session.commit()
    return len(stuck)


@lru_cache(maxsize=1)
def get_pipeline() -> Pipeline:
    return Pipeline(llm=StructuredLLM(), criteria=load_criteria())
