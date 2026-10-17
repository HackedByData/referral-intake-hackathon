"""Orchestrates one referral: transcribe -> evaluate -> verify -> decide -> hold bed. Runs as a background task."""
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
from app.rules.criteria import Criteria, load_criteria
from app.rules.evaluate import evaluate_packet
from app.rules.prompts import PROMPT_VERSION
from app.rules.tree import decide
from app.rules.verify import verify_results, verify_summary
from app.schemas import AnalysisResult
from app.workflow import audit
from app.workflow.models import Referral

log = logging.getLogger(__name__)
IN_FLIGHT = ("uploaded", "transcribing", "analyzing")


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Pipeline:
    def __init__(self, llm, criteria: Criteria, engine_getter=get_engine, settings: Settings | None = None,
                 transcribe=transcribe_packet, evaluate=evaluate_packet):
        self.llm = llm
        self.criteria = criteria
        self.engine_getter = engine_getter
        self.settings = settings or get_settings()
        self.transcribe = transcribe
        self.evaluate = evaluate

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
            summary, results, timings = await self.evaluate(self.llm, packet, self.criteria,
                                                             concurrency=self.settings.rules_concurrency)
            results = verify_results(results, packet)
            summary = verify_summary(summary, packet)
            with Session(self.engine_getter()) as s:
                options = beds.find_matches(s, summary.bed_needs, for_referral=referral_id)
                rec = decide(self.criteria.rules, results, summary, options)
                if rec.held_bed:
                    try:
                        beds.hold(s, rec.held_bed.bed_id, referral_id, self.settings.hold_ttl_hours)
                    except beds.BedUnavailable:
                        log.warning("Bed %s taken between match and hold", rec.held_bed.bed_id)
                result = AnalysisResult(
                    prompt_version=PROMPT_VERSION, model=getattr(self.llm, "model", "unknown"), packet=packet,
                    summary=summary, rule_results=results, recommendation=rec,
                    timings={"transcribe": t1 - t0, **timings, "total": time.perf_counter() - t0})
                ref = s.get(Referral, referral_id)
                ref.result_json, ref.status, ref.progress, ref.updated_at = (
                    result.model_dump_json(), "ready_for_review", None, _now())
                s.add(ref)
                s.commit()
                audit.record(s, "analysis_complete", referral_id=referral_id, ai_recommendation=rec.decision,
                             held_bed=rec.held_bed.bed_id if rec.held_bed else None,
                             seconds=round(time.perf_counter() - t0, 1))
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
