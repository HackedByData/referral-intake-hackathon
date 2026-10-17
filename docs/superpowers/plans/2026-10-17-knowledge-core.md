# Knowledge Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development.

**Goal:** Capture coordinator-vs-AI disagreements as staff-approved lessons and use them (rule guidance, bed re-ranking,
county mapping, precedents) transparently.
**Spec:** [`docs/superpowers/specs/2026-10-17-knowledge-core-design.md`](../specs/2026-10-17-knowledge-core-design.md)
**Base:** branch `feat/knowledge-core` from `main@699eca9`. Step 0 is done: the schemas in `app/schemas.py`
(`CaseProfile`, `Precedent`, `LessonView`, `LessonReview`, `LessonKind`, `LessonStatus`, `Recommendation.precedents`,
`AnalysisResult.knowledge_used`/`profile`, `TreeStep.node` "knowledge").

## Global Constraints
Everything in `docs/superpowers/plans/2026-10-17-referral-intake.md` Global Constraints, including:
- NaiveDatetime table fields;
- no network in tests;
- invented test text only;
- never commit `data/`, `.env` or `*.xlsx`.

Lessons hold no packet quotes or names.

## Review Focus
- A rejected, proposed or retired lesson never influences anything.
- Bed re-ranking never introduces a bed that `find_matches` filtered out (county/program/availability/sex/ADA).
- Rule guidance goes in the per-group task text, NOT the cached packet prefix (`packet_block` must stay byte-identical).
- Approving the same decision twice (409) creates no duplicate lessons.
- `eval/run.py` uses no knowledge unless `--with-knowledge`.

---

### Task K: Knowledge core (one implementer, TDD)

**Files**
- Create:
  - `app/knowledge/__init__.py`
  - `app/knowledge/models.py`: `Lesson` table: id, kind, status, created_at (NaiveDatetime), created_by, source_referral_id, profile_json, payload_json, note, needs_note, reviewed_by, reviewed_at (NaiveDatetime|None), times_applied, last_applied_at (NaiveDatetime|None)
  - `app/knowledge/profile.py`
  - `app/knowledge/store.py`
  - `app/knowledge/capture.py`
  - `app/knowledge/apply.py`
  - `app/api/knowledge.py`
  - `tests/test_knowledge_*.py`
- Modify:
  - `app/db.py` (`init_db` imports `app.knowledge.models`)
  - `app/main.py` (include router)
  - `app/workflow/decisions.py` (capture hooks)
  - `app/api/referrals.py` (county capture + apply on re-match)
  - `app/workflow/pipeline.py` (apply)
  - `app/rules/evaluate.py` (rule guidance in group task text)
  - `app/rules/county.py` (merged plan map)
  - `app/rules/tree.py` (knowledge step)
  - `eval/run.py` (`--with-knowledge` flag)
  - `docs/api-contract.md` (v3)
  - `docs/modules/knowledge.md`, README "What we built" (2–3 lines)

**Interfaces**
- `profile.build_profile(result: AnalysisResult, rules: list[Rule]) -> CaseProfile`
  - county = `summary.bed_needs.county`
  - payer = `summary.health_plan`
  - sex / wheelchair / oxygen / isolation / mobility_aid from `bed_needs`
  - systems = sorted unique `section.removesuffix(" (eligible)")` of `by_system` rules (kind `eligible`) with status pass
  - flagged_rule_ids = sorted rule IDs with status `fail` or `unknown` among kinds must/exclude/conditional/document
- `apply.similarity(a: CaseProfile, b: CaseProfile) -> float`, per the spec's weights.
- `store`:
  - `create(session, kind, profile, payload, note, created_by, referral_id) -> Lesson` (`needs_note = not note.strip()`)
  - `list_lessons(session, status=None, kind=None) -> list[LessonView]`
  - `review(session, id, action: Literal["approve","reject","retire"], LessonReview) -> LessonView` (409 rules per the spec; an edited note replaces the note and clears `needs_note`)
  - `approved(session, kind) -> list[Lesson]`
  - `mark_applied(session, ids)`
  - `to_view(lesson) -> LessonView` with a human `summary` string per kind
- `capture.from_decision(session, referral_id, result, decision: CoordinatorDecision, chosen_bed_id: str|None, held_bed_id: str|None, criteria) -> list[int]`
  - called in `apply_decision` AFTER the referral commit succeeds, for every decision kind, in its own try/except that logs and never breaks the decision;
  - creates `decision_precedent` if `AGREES[decision.decision] != ai`;
  - creates one `rule_interpretation` per override whose status differs from the AI result's status for that rule;
  - creates `bed_preference` if approve and `chosen_bed_id != held_bed_id` and both are not None.
  - Capture `held_bed_id` BEFORE `assign`, since assign deactivates the hold.
- `capture.from_county(session, referral_id, result, old: CountySuggestion|None, new_county, coordinator)`
  - creates `county_mapping` when `summary.health_plan` is set and `(old.county or "")` differs, case-insensitively, from `new_county`.
- `apply`:
  - `rule_guidance(session, rules: list[Rule], profile) -> tuple[dict[str, list[str]], list[int]]`
    - maps rule_id to up to 3 guidance lines, score ≥ 0.3, best first;
    - line format: `f"Lesson #{id}: AI said {ai_status}, coordinator decided {coordinator_status}. Coordinator note: {note or '(none)'}"`.
  - `rerank_beds(session, options: list[BedOption], profile) -> tuple[list[BedOption], list[int]]`
    - for each approved bed_preference with score ≥ 0.5: +score for options at the chosen facility, -score at the held facility (when they differ);
    - stable sort by -total;
    - append a match note on boosted options.
  - `plan_map_additions(session) -> tuple[dict[str, list[str]], dict[str, int]]`: normalized plan → [county], plan → lesson id.
  - `precedents(session, profile, limit=3) -> list[Precedent]`: approved decision_precedent lessons, score ≥ 0.5.

**Wiring**
- `evaluate_packet(llm, packet, criteria, concurrency=8, guidance: dict[str, list[str]] | None = None)`
  - `evaluate_group` appends, after the rules list in the TASK text only:
    `"\n\nCoordinator guidance from past cases (approved by IHH staff; use it to interpret the rule, still cite this packet's evidence):\n- RULE-ID: line\n..."`
    for the group's rules that have guidance.
  - The guidance is built BEFORE evaluation, from a provisional profile, because full results aren't known yet. Use a profile from
    the summary only (county, payer, bed needs). Systems and flagged rules are empty at that point; that's acceptable.
- Pipeline order:
  1. transcribe
  2. summarize
  3. suggestion/county (moved to before rule evaluation)
  4. guidance
  5. evaluate rule groups
  6. verify
  7. `profile = build_profile(...)`
  8. find_matches with `limit=10`
  9. `rerank_beds`, then keep the first 3
  10. decide
  11. `rec.precedents = precedents(...)`
  12. append a `TreeStep(node="knowledge", outcome="triggered" if used else "clear", rule_ids=[], explanation="Used approved lessons #..." or "No approved lessons applied.")`
  13. `result.knowledge_used`, `result.profile`
  14. `mark_applied`

  This needs a summary-first split in `evaluate_packet`. Simplest: keep `evaluate_packet`, but have the pipeline call
  `summarize` then `evaluate_rules(llm, packet, criteria, guidance=...)`. Refactor `evaluate_packet` into these two
  functions, keeping `evaluate_packet` as a wrapper so the eval and existing tests still work.
- County: `suggest_county(summary, plan_map)`. The pipeline passes `{**load_plan_map(), **additions}`. When the match came
  from an addition, the basis appends `" (learned from lesson #N)"` and N is added to knowledge_used.
- County route: call `capture.from_county`, and apply `rerank_beds` + `precedents` on re-match.
- `eval/run.py`: `--with-knowledge` flag, default off. When off, no guidance and no plan additions.

**Tests (all offline, FakeLLM / in-memory DB)**
- profile: build from a factory AnalysisResult (systems + flagged ids).
- similarity: identical profiles = 1.0; disjoint < 0.3; None fields don't count against.
- store: create → proposed + `needs_note`; approve/reject/retire transitions; illegal transitions → 409 at the API level; an edited note clears `needs_note`; audit records.
- capture via `apply_decision`:
  - deny when AI approve → decision_precedent;
  - override MOB-3 pass→fail → rule_interpretation;
  - approve with a different bed_id → bed_preference;
  - agreeing approve → no lessons;
  - double submit (409) → no duplicate lessons.
- county capture via the API route.
- apply:
  - proposed/rejected lessons are ignored;
  - an approved bed_preference boosts the chosen facility among already-filtered options and never adds new options;
  - rule guidance shows up in the group task text for that rule ID and NOT in `packet_block`;
  - a county addition makes `suggest_county` return the learned county with "learned from lesson #N";
  - precedents threshold.
- pipeline: with one approved bed_preference + one rule_interpretation, `knowledge_used` contains both ids, there's a knowledge TreeStep, and `times_applied` was incremented.
- eval: `run_one` without the flag passes no guidance (assert via FakeLLM content).
