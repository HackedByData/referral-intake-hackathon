# Knowledge core (`app/knowledge/`, `app/api/knowledge.py`)

Spec: `docs/superpowers/specs/2026-10-17-knowledge-core-design.md`. Plan: `docs/superpowers/plans/2026-10-17-knowledge-core.md`.

**Idea.** When a coordinator's call differs from the AI's, capture a *lesson*. Staff approve it. Approved lessons
then influence future recommendations, and every use is visible.

**Safety rules**
- Only `approved` lessons are ever read (`store.approved`). Proposed, rejected and retired lessons do nothing.
- Lessons are de-identified: a coarse `CaseProfile`, rule IDs, bed facility attributes and the coordinator's note. No packet quotes, no names. They live in `data/app.db` (gitignored).
- Lessons never bypass hard rules. Bed lessons re-rank only beds that `find_matches` already returned. Rule lessons add guidance to that rule's prompt; the deterministic tree and the human decision are unchanged. Precedents are shown to the coordinator, never fed to the model.

**Modules**
- `models.py`: `Lesson` table. `profile.py`: `build_profile` (full) and `from_summary` (before rules are evaluated).
- `store.py`: create / list / review (approve, reject, retire; 409 on illegal transitions) / `approved` / `mark_applied`. Every action is audited (`lesson_proposed|approved|rejected|retired`).
- `capture.py`: `from_decision` (called by `apply_decision` after the referral commit, in its own try/except; `held_bed_id` is read before `assign`) and `from_county` (county route).
- `apply.py`: `similarity`, `rule_guidance`, `rerank_beds`, `plan_map_additions`, `precedents`, `attach` (adds precedents and the `knowledge` tree step).

**Capture hygiene.** A repeat decision (e.g. request_info twice, or request_info then approve) never duplicates a lesson: capture skips a lesson when a non-rejected one from the same referral has the same key (rule_id + status; coordinator decision; chosen facility; coordinator county). County lessons compare against `CountySuggestion.ai_county` (preserved across confirms), not the previous confirm, and are not proposed for built-in plans that map to 0 or several counties (IEHP, Molina, ...). `rule_interpretation` payloads hold no AI free text.

**Re-match.** The county route keeps inherited rule-guidance lessons, keeps the county lesson only if the confirmed county equals the learned one, recomputes bed/precedent lessons, and calls `mark_applied` only for newly applied ids.

**Lesson kinds.** `decision_precedent` (final decision differs from the AI), `rule_interpretation` (override changes a rule status), `bed_preference` (approve with a bed other than the held one), `county_mapping` (county differs from the AI suggestion).

**Similarity** is a weighted structured match: county .25, payer .15, sex .10, wheelchair .10, oxygen .05, isolation .05, body-system Jaccard .15, flagged-rule Jaccard .15. Fields that are unknown (`None`) on either side are dropped and the weights renormalized. `systems` and `flagged_rule_ids` are `None` before rules are evaluated; afterwards an empty list is a real value (both empty = 1.0, one empty = 0.0). Thresholds: 0.5 for precedents and bed preferences, 0.3 for rule guidance (max 3 lines per rule).

**Where it plugs in**
- `pipeline.py`: transcribe, summarize (+ verify), county (built-in map merged with learned additions; sets `ai_county`), guidance (profile includes the county), evaluate rule groups, verify, profile, `find_matches(limit=10)`, `rerank_beds`, top 3, decide, precedents, `knowledge` tree step, `knowledge_used`, `mark_applied`.
- `rules/evaluate.py`: guidance is appended to the per-group *task* text only. `packet_block` (the cached prefix) is byte-identical with or without lessons.
- `rules/county.py`: `merge_plan_map`, `learned_lesson_id`; the basis gets " (learned from lesson #N)".
- `POST /api/referrals/{id}/county` captures `county_mapping` and applies `rerank_beds` + precedents on re-match.
- `eval/run.py`: no lessons unless `--with-knowledge`, to avoid self-leakage into the scorecard.

**Testing:** `pytest tests/test_knowledge_*.py`. All offline (FakeLLM, in-memory SQLite).
