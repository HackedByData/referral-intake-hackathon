# Rules engine (`app/rules/`)

**Purpose:** turn `PacketText` into a cited, rule-by-rule judgment and a deterministic recommendation.

**Flow:** `evaluate_packet` (Claude: 1 summary call + 8 parallel rule-group calls, shared cached prefix) →
`verify_results` / `verify_summary` (deterministic quote check) → `decide` (deterministic tree).

**Files**
- `criteria.py`: 155 rules from `criteria/admission-criteria.json` (51 outline incl. GEN-1..3, 81 body-system,
  10 checklist, 13 referral-form; 3 form rules superseded → fixed `not_applicable`). 8 groups of ≤30 rules.
- `prompts.py`: all prompt text; **bump `PROMPT_VERSION` on any change**.
- `evaluate.py`: coverage guarantee (missing → `unknown`/low confidence, invented IDs dropped). A failed group with
  more than 6 rules is split in half and retried once.
- `verify.py`: normalized substring, or rapidfuzz `partial_ratio >= 90` for quotes of 12+ chars. Checks the cited page ±1.
- `tree.py`: order = exclusions → must → general eligibility → human review → missing info → unverified →
  conflicts → bed. Deny needs **verified** evidence. OTH-2 never denies. `ask` never blocks.

**Guardrails:** a `must` pass (GEN-* included) with no verified quote forces needs_review (exclude passes exempt); fuzzy quote matches additionally require every number and negation token to appear on the page; any non-LLMError from a group call yields all-unknown for that group instead of crashing the run.

**Number guard:** fuzzy matches extract numbers with a regex (so `98%` and `150ft` are checked) and require each to appear on the page; negation tokens are compared with trailing periods stripped.

**Silent not_applicable:** a `must` rule whose text does not start with "If" that comes back `not_applicable` without verified evidence adds an `unverified_evidence` review reason (GEN-*, human-review and superseded rules exempt); MOB-3 and MOB-4 both `not_applicable` also forces review.

**Status semantics:** always from the eligibility point of view (see `app/schemas.py` and `RULES_TASK`).

**Testing:** `pytest tests/test_criteria.py tests/test_verify.py tests/test_tree.py tests/test_evaluate.py`.

**Known limits / tuning log:** add findings from eval runs here (rule-ID terms only, never packet quotes).
