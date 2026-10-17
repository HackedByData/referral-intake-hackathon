# Addendum: knowledge core (learning from coordinator overrides)

**Date:** 2026-10-17 · **Status:** Approved in chat · **Extends:** the referral-intake spec and the BMS/county addendum

## Goal
When a coordinator's decision differs from the AI's, the system captures it as a **lesson**. Once a lesson is
**approved by staff**, future recommendations consult it, and every recommendation shows which lessons it used.

## Guardrails
- Lessons are proposed automatically but used **only after staff approval**.
- Lessons never bypass hard rules. Bed lessons only re-rank beds that already passed the county, program,
  availability, sex and ADA filters. Rule lessons only add guidance to the model's evaluation of that rule ID; the
  deterministic tree, verified-evidence denials and the human final decision are unchanged. Decision precedents are
  **shown to the coordinator, not fed to the model**.
- Lessons are de-identified: a coarse `CaseProfile` (county, payer, sex, wheelchair/oxygen/isolation, mobility aid,
  body systems, flagged rule IDs), coordinator notes and rule IDs. No packet quotes and no names. They are stored in
  `data/` (gitignored).
- Transparency: `AnalysisResult.knowledge_used` (lesson ids), `Recommendation.precedents`, a "knowledge" tree step,
  bed match notes ("Preferred for similar cases (lesson #N)"), and a county basis ("learned from lesson #N").
- The eval runs without knowledge by default (`--with-knowledge` opts in), to avoid self-leakage.

## Lesson kinds
| Kind | Captured when | Payload | Used as |
|---|---|---|---|
| `decision_precedent` | final decision ≠ AI recommendation (`agreed_with_ai == False`) | `ai_decision`, `coordinator_decision`, `ai_rule_ids` (triggered/flagged) | "Similar past cases" on the review card |
| `rule_interpretation` | an override sets a rule status different from the AI's | `rule_id`, `ai_status`, `coordinator_status`, `ai_reasoning` (first sentence) | guidance appended to that rule's evaluation prompt |
| `bed_preference` | approve with `bed_id` ≠ held bed | `held` / `chosen`: `{facility_id, facility, county, sex_designation, wheelchair_accessible, oxygen_ok, isolation_ok, ground_floor}` | re-rank bed options |
| `county_mapping` | county confirm ≠ AI suggestion | `health_plan`, `ai_county`, `coordinator_county` | merged into the plan→county map |

A lesson with no coordinator note gets `needs_note=True`. It can still be approved, and the reviewer may add a note.

## Similarity (structured, deterministic)
`score(profile_a, profile_b) ∈ [0,1]` is a weighted match:
- county 0.25
- payer 0.15
- sex 0.1
- wheelchair 0.1
- oxygen 0.05
- isolation 0.05
- body systems Jaccard 0.15
- flagged-rule Jaccard 0.15

`None` values on either side don't count toward the denominator (renormalized). Threshold 0.5 for precedents and
bed preferences. Rule lessons are keyed by rule ID: up to 3 per rule, by score, threshold 0.3.

## API
- `GET /api/knowledge?status=&kind=` → `LessonView[]`
- `POST /api/knowledge/{id}/approve|reject|retire` (`LessonReview`)
- `GET /api/knowledge/export` → JSON

Approve/reject only from `proposed`; retire only from `approved` (otherwise 409). All actions are audited.
