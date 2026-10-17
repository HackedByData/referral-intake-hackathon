"""Use approved lessons. Every function here reads only `approved` lessons (store.approved)."""
import json

from sqlmodel import Session

from app.beds.models import Bed
from app.knowledge import store
from app.knowledge.models import Lesson
from app.rules.county import normalize
from app.schemas import BedOption, CaseProfile, Precedent, Recommendation, Rule, TreeStep

GUIDANCE_THRESHOLD = 0.3
MATCH_THRESHOLD = 0.5


def _norm(s: str | None) -> str | None:
    return s.strip().lower().removesuffix(" county") if s and s.strip() else None


def _eq(x, y) -> float | None:
    return None if x is None or y is None else float(x == y)


def _jaccard(a: list[str] | None, b: list[str] | None) -> float | None:
    if a is None or b is None:  # None means "not evaluated yet": not held against the match
        return None
    sa, sb = set(a), set(b)
    return 1.0 if not sa and not sb else len(sa & sb) / len(sa | sb)


def similarity(a: CaseProfile, b: CaseProfile) -> float:
    """Weighted structured match in [0,1]. Fields unknown on either side are dropped and weights renormalized."""
    parts: list[tuple[float, float | None]] = [
        (0.25, _eq(_norm(a.county), _norm(b.county))),
        (0.15, _eq(_norm(a.payer), _norm(b.payer))),
        (0.10, _eq(a.sex if a.sex != "unknown" else None, b.sex if b.sex != "unknown" else None)),
        (0.10, _eq(a.wheelchair, b.wheelchair)),
        (0.05, _eq(a.oxygen, b.oxygen)),
        (0.05, _eq(a.isolation, b.isolation)),
        (0.15, _jaccard(a.systems, b.systems)),
        (0.15, _jaccard(a.flagged_rule_ids, b.flagged_rule_ids)),
    ]
    live = [(w, s) for w, s in parts if s is not None]
    total = sum(w for w, _ in live)
    return round(sum(w * s for w, s in live) / total, 4) if total else 0.0


def _profile(lesson) -> CaseProfile:
    return CaseProfile.model_validate_json(lesson.profile_json)


def rule_guidance(session: Session, rules: list[Rule], profile: CaseProfile) -> tuple[dict[str, list[str]], list[int]]:
    """rule_id -> up to 3 guidance lines (best match first) from approved rule_interpretation lessons."""
    wanted = {r.id for r in rules}
    scored: dict[str, list[tuple[float, int, str]]] = {}
    for lesson in store.approved(session, "rule_interpretation"):
        p = json.loads(lesson.payload_json)
        rid = p.get("rule_id")
        if rid not in wanted:
            continue
        score = similarity(_profile(lesson), profile)
        if score < GUIDANCE_THRESHOLD:
            continue
        line = (f"Lesson #{lesson.id}: AI said {p.get('ai_status')}, coordinator decided "
                f"{p.get('coordinator_status')}. Coordinator note: {lesson.note or '(none)'}")
        scored.setdefault(rid, []).append((score, lesson.id, line))
    guidance: dict[str, list[str]] = {}
    used: list[int] = []
    for rid, items in scored.items():
        items.sort(key=lambda x: (-x[0], x[1]))
        guidance[rid] = [line for _, _, line in items[:3]]
        used += [lid for _, lid, _ in items[:3]]
    return guidance, sorted(set(used))


def rerank_beds(session: Session, options: list[BedOption], profile: CaseProfile) -> tuple[list[BedOption], list[int]]:
    """Stable re-rank of ALREADY-FILTERED options. Never adds, removes or filters anything."""
    if not options:
        return options, []
    facility = {}
    for o in options:
        bed = session.get(Bed, o.bed_id)
        facility[o.bed_id] = bed.site_id if bed else None
    totals = [0.0] * len(options)
    notes: list[list[str]] = [[] for _ in options]
    used: list[int] = []
    for lesson in store.approved(session, "bed_preference"):
        score = similarity(_profile(lesson), profile)
        if score < MATCH_THRESHOLD:
            continue
        p = json.loads(lesson.payload_json)
        chosen, held = p.get("chosen", {}).get("facility_id"), p.get("held", {}).get("facility_id")
        if not chosen or not held or chosen == held:
            continue
        touched = False
        for i, o in enumerate(options):
            if facility[o.bed_id] == chosen:
                totals[i] += score
                notes[i].append(f"Preferred for similar cases (lesson #{lesson.id})")
                touched = True
            elif facility[o.bed_id] == held:
                totals[i] -= score
                touched = True
        if touched:
            used.append(lesson.id)
    order = sorted(range(len(options)), key=lambda i: -totals[i])  # stable
    out = [options[i].model_copy(update={"match_notes": options[i].match_notes + notes[i]}) if notes[i] else options[i]
           for i in order]
    return out, sorted(set(used))


def plan_map_additions(session: Session) -> tuple[dict[str, list[str]], dict[str, int]]:
    """Approved county_mapping lessons as {normalized plan: [county]} plus {normalized plan: lesson id}. Latest wins."""
    additions: dict[str, list[str]] = {}
    ids: dict[str, int] = {}
    for lesson in store.approved(session, "county_mapping"):
        p = json.loads(lesson.payload_json)
        key = normalize(p.get("health_plan") or "")
        if key and p.get("coordinator_county"):
            additions[key], ids[key] = [p["coordinator_county"]], lesson.id
    return additions, ids


def precedents(session: Session, profile: CaseProfile, limit: int = 3) -> list[Precedent]:
    """Similar past decisions that differed from the AI: shown to the coordinator, never fed to the model."""
    found = []
    for lesson in store.approved(session, "decision_precedent"):
        score = similarity(_profile(lesson), profile)
        if score >= MATCH_THRESHOLD:
            p = json.loads(lesson.payload_json)
            found.append(Precedent(lesson_id=lesson.id, similarity=score, summary=store.summary_text(lesson.kind, p),
                                   ai_decision=p.get("ai_decision", ""),
                                   coordinator_decision=p.get("coordinator_decision", ""), note=lesson.note))
    found.sort(key=lambda x: (-x.similarity, x.lesson_id))
    return found[:limit]


def inherited_ids(session: Session, ids: list[int], confirmed_county: str) -> list[int]:
    """Lessons from an earlier analysis that still hold after the county is confirmed: rule guidance always,
    a county mapping only when its learned county is the confirmed one. Bed lessons are recomputed, never inherited."""
    keep = []
    for lid in ids:
        lesson = session.get(Lesson, lid)
        if lesson is None or lesson.status != "approved":
            continue
        if lesson.kind == "rule_interpretation":
            keep.append(lid)
        elif lesson.kind == "county_mapping" and _norm(json.loads(lesson.payload_json).get("coordinator_county"))                 == _norm(confirmed_county):
            keep.append(lid)
    return keep


def attach(rec: Recommendation, precedent_list: list[Precedent], used: list[int]) -> Recommendation:
    """Add precedents and the transparent 'knowledge' tree step to a recommendation."""
    used = sorted(set(used))
    step = TreeStep(node="knowledge", outcome="triggered" if used else "clear", rule_ids=[],
                    explanation=("Used approved lessons " + ", ".join(f"#{i}" for i in used) + "."
                                 if used else "No approved lessons applied."))
    return rec.model_copy(update={"precedents": precedent_list, "tree": rec.tree + [step]})
