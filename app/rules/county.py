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


def builtin_plan_counties(plan: str) -> list[str] | None:
    """Counties the BUILT-IN map lists for this plan name, or None if the plan is not in it."""
    key = _match_key(plan, load_plan_map()) if plan.strip() else None
    return None if key is None else load_plan_map()[key]


def merge_plan_map(base: dict[str, list[str]], additions: dict[str, list[str]]) -> dict[str, list[str]]:
    """Learned mappings replace built-in keys that normalize the same way."""
    drop = {normalize(k) for k in additions}
    return {**{k: v for k, v in base.items() if normalize(k) not in drop}, **additions}


def learned_lesson_id(summary: CaseSummary, plan_map: dict, learned: dict[str, int] | None) -> int | None:
    """Lesson id when this summary's plan resolved through a learned mapping, else None."""
    plan = (summary.health_plan or "").strip()
    key = _match_key(plan, plan_map) if plan else None
    return (learned or {}).get(normalize(key)) if key is not None else None


def suggest_county(summary: CaseSummary, plan_map: dict | None = None,
                   learned: dict[str, int] | None = None) -> CountySuggestion:
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
        lesson_id = learned_lesson_id(summary, plan_map, learned)
        if lesson_id is not None:
            basis += f" (learned from lesson #{lesson_id})"
        return CountySuggestion(county=counties[0], candidates=list(counties), basis=basis)
    if len(counties) > 1:
        return CountySuggestion(candidates=list(counties),
                                basis=f"Health plan '{plan}' serves several counties: {', '.join(counties)}")
    return CountySuggestion(basis=f"Health plan '{plan}' does not map to a single county")
