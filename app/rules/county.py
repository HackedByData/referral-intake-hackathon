"""Deterministic plan -> county suggestion. The AI only extracts the plan name; this code does the mapping."""
import json
import re
from functools import lru_cache
from pathlib import Path

from app.schemas import CaseSummary, CountySuggestion

PLAN_FILE = Path(__file__).resolve().parents[2] / "criteria" / "plan_county.json"
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize(s: str) -> str:
    return _NON_ALNUM.sub(" ", s.lower()).strip()


@lru_cache(maxsize=1)
def load_plan_map() -> dict[str, list[str]]:
    return json.loads(PLAN_FILE.read_text(encoding="utf-8"))["plans"]


def _pages(evidence) -> str:
    pages = sorted({e.page for e in evidence})
    return f" (p.{', '.join(map(str, pages))})" if pages else ""


def _match_key(plan: str, plan_map: dict[str, list[str]]) -> str | None:
    padded = f" {normalize(plan)} "
    hits = [k for k in plan_map if f" {normalize(k)} " in padded]
    return max(hits, key=lambda k: len(normalize(k))) if hits else None  # longest key wins


def suggest_county(summary: CaseSummary, plan_map: dict | None = None) -> CountySuggestion:
    plan_map = load_plan_map() if plan_map is None else plan_map
    plan = (summary.health_plan or "").strip()
    if not plan:
        basis = "No health plan named in the packet."
        for c in summary.location_clues:
            basis += f"; location clues: '{c.quote}' (p.{c.page})"
        return CountySuggestion(basis=basis)
    key = _match_key(plan, plan_map)
    counties = plan_map[key] if key is not None else []
    if len(counties) == 1:
        verified = [e for e in summary.health_plan_evidence if e.verified]
        basis = f"Health plan '{plan}'{_pages(verified or summary.health_plan_evidence)} serves {counties[0]} County"
        if not verified:
            basis += " — quote not verified"
        return CountySuggestion(county=counties[0], candidates=list(counties), basis=basis)
    if len(counties) > 1:
        return CountySuggestion(candidates=list(counties),
                                basis=f"Health plan '{plan}' serves several counties: {', '.join(counties)}")
    return CountySuggestion(basis=f"Health plan '{plan}' does not map to a single county")
