"""Progress percent and time-remaining estimate for a referral being analyzed.

The pipeline stores the raw state (stage, wall-clock start times, counts, estimates); the API computes percent and
time left at read time, so the countdown keeps moving between pipeline events. Estimates come from how long earlier
packets actually took (median), falling back to the 2026-10-17 eval baseline. Page reading uses the live page rate.
"""
import statistics
import time
from dataclasses import asdict, dataclass

from sqlmodel import Session, select

from app.schemas import AnalysisResult
from app.workflow.models import Referral

DEFAULT_SEC_PER_PAGE = 4.0   # eval baseline, 8 pages at a time
DEFAULT_SUMMARY_SEC = 40.0
DEFAULT_RULES_SEC = 45.0     # 8 rule groups run in parallel, so they finish close together
CACHED_PAGE_SEC = 0.5        # faster than this per page means the OCR came from cache: ignore for the rate
MIN_LEFT_SEC = 3.0


@dataclass(frozen=True)
class Estimates:
    sec_per_page: float = DEFAULT_SEC_PER_PAGE
    summary_sec: float = DEFAULT_SUMMARY_SEC
    rules_sec: float = DEFAULT_RULES_SEC


def historical_estimates(session: Session, limit: int = 10) -> Estimates:
    refs = session.exec(select(Referral).where(Referral.result_json.is_not(None))
                        .order_by(Referral.updated_at.desc()).limit(limit)).all()
    per_page, summary, rules = [], [], []
    for ref in refs:
        try:
            result = AnalysisResult.model_validate_json(ref.result_json)
        except Exception:
            continue
        t, pages = result.timings, result.packet.page_count
        if pages and t.get("transcribe", 0) / pages >= CACHED_PAGE_SEC:
            per_page.append(t["transcribe"] / pages)
        if "summary" in t:
            summary.append(t["summary"])
        if "rules" in t:
            rules.append(t["rules"])
    med = lambda xs, d: statistics.median(xs) if xs else d  # noqa: E731
    return Estimates(med(per_page, DEFAULT_SEC_PER_PAGE), med(summary, DEFAULT_SUMMARY_SEC),
                     med(rules, DEFAULT_RULES_SEC))


def new_state(est: Estimates, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    return {"stage": "transcribing", "phase": "pages", "t0": now, "phase_t0": now, "pages_done": 0,
            "pages_total": 0, "groups_done": 0, "groups_total": 0, "est": asdict(est)}


def estimate(state: dict, now: float | None = None) -> dict:
    """Percent (1-99) and seconds left for a stored progress state, computed at `now`."""
    now = time.time() if now is None else now
    est = Estimates(**state["est"])
    elapsed = max(now - state["t0"], 0.0)
    phase_elapsed = max(now - state["phase_t0"], 0.0)
    phase = state["phase"]
    if phase == "pages":
        done, total = state["pages_done"], state["pages_total"]
        if done and total:
            reading_left = phase_elapsed / done * (total - done)
        else:
            reading_left = est.sec_per_page * (total or 20) - phase_elapsed
        left = max(reading_left, 0) + est.summary_sec + est.rules_sec
        detail = f"Reading page {done} of {total}" if total else "Reading pages"
    elif phase == "summary":
        left = max(est.summary_sec - phase_elapsed, MIN_LEFT_SEC) + est.rules_sec
        detail = "Summarizing the packet"
    else:
        left = est.rules_sec - phase_elapsed
        detail = f"Checking admission criteria ({state['groups_done']} of {state['groups_total']} groups)"
    left = max(left, MIN_LEFT_SEC)
    percent = min(99, max(1, round(100 * elapsed / (elapsed + left))))
    return {"percent": percent, "eta_seconds": int(round(left)), "detail": detail}
