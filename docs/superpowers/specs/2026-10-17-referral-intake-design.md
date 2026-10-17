# Referral Intake + Bed Management: Design Spec

**Date:** 2026-10-17 · **Status:** Approved in chat, pending written review · **Owner:** backend/AI (Devin); frontend by teammate (Claude Design)
**Context:** [`docs/context/challenge-brief.md`](../../context/challenge-brief.md) (read first)

## 1. Goal

A coordinator at IHH uploads a faxed referral packet (image-only PDF). Within a few minutes they see:

- a **recommendation** (`approve` / `deny` / `needs_review`),
- a **proposed bed** that has already been held for them,
- the **top reasons** in plain language, each traceable to a rule ID and a page of the packet,
- for `needs_review`, a **drafted list of questions to call the hospital about**.

The coordinator makes the final decision. A "details" view shows every rule judgment, the evidence quote with its page,
the decision-tree path, and contradictions found inside the chart. On approval the held bed becomes an assignment and an
admission record is written to a **mock Kipu outbox**.

A bed management system (BMS) replaces IHH's Excel sheet: beds, attributes, availability, holds, assignments,
discharge/release, CSV/XLSX import.

### Success criteria

1. Any of the 11 packets, **or an unseen packet handed over by judges**, runs end to end without manual steps in under ~5 minutes.
2. Every `fail` that drives a denial cites a page and a quote that is **verified** to exist on that page.
3. The eval harness reports agreement with IHH's decisions on all 11 packets. **False approvals** (we approve, IHH denied) are the headline metric and should be 0.
4. Date-dependent rules come back `unknown` ("can't tell from this packet"), never guessed.
5. Approval is impossible without a bed that is actually available, and is atomic (no double booking).

### Non-goals (today)

- A real Kipu connection. **The event rules forbid connecting to IHH systems**, so it is mocked behind an interface.
- Sending any fax, email or SMS. Drafts only.
- Children's criteria (no sample packets).
- Auth and multi-user permissions. The coordinator's name is a free-text field.
- Frontend UI, which the teammate builds against [`docs/api-contract.md`](../../api-contract.md).

## 2. Hard constraints

- **Packets never enter git.** They are read from `PACKETS_DIR` or uploaded at runtime into `DATA_DIR` (gitignored).
  Generated artifacts that contain packet text (OCR markdown, results) live only under `DATA_DIR`.
  The committed eval scorecard contains packet IDs, decisions and rule IDs only, never quotes.
- The pipeline **never reads `eval/decisions.json`**. Only `eval/run.py` reads it, from a path passed at the command line.
- No invented facts. Prompts instruct "unknown if not stated", and the verifier enforces it for quotes.
- Redaction boxes are "removed for privacy", never a missing document.
- `ANTHROPIC_API_KEY` comes from the environment (`.env`, gitignored). `.env.example` ships with it empty.

## 3. Stack

- Python 3.12+ (dev machine runs 3.14), **FastAPI**, **SQLModel** (SQLite), **PyMuPDF** (page rendering/rotation),
  **anthropic** Python SDK (async client), **rapidfuzz** (quote verification), **openpyxl** (XLSX import), **pytest**.
- Model: `claude-opus-5-5` for all calls (skill default), adaptive thinking, explicit `output_config.effort`
  (`low` for OCR, `high` for rule evaluation). Structured outputs via `client.messages.parse(output_format=PydanticModel)`.
  Every call checks `stop_reason == "refusal"` before reading content. If the SDK accepts it, enable server-side fallbacks
  (`betas=["server-side-fallback-2026-07-01"]`, `fallbacks="default"`); otherwise treat a refusal as a stage failure
  with a clear error.
- Runs locally (`uvicorn`). CORS open for the frontend dev server.

## 4. Repository layout

```
app/
  main.py                 FastAPI app, CORS, router registration, startup (DB init, seed beds if empty)
  config.py               Settings from env: ANTHROPIC_API_KEY, DATA_DIR, PACKETS_DIR, MODEL, HOLD_TTL_HOURS, OCR_CONCURRENCY
  schemas.py              ALL shared Pydantic models (section 5). Single source of truth for every workstream.
  db.py                   SQLModel engine/session, table models for referrals + audit
  llm.py                  Thin wrapper: async structured call with refusal handling, retries, prompt caching
  ingest/
    render.py             PDF -> page PNGs (PyMuPDF), rotation support, page-image cache
    ocr.py                page image -> PageMarkdown via Claude vision; orientation retry; PacketText cache by sha256
  rules/
    criteria.py           Load criteria/admission-criteria.json -> flat list[Rule]; rule groups
    evaluate.py           CaseSummary call + parallel RuleGroup calls -> list[RuleResult]
    verify.py             Quote verification (deterministic) -> sets Evidence.verified
    tree.py               Deterministic decision tree -> Recommendation (pure function)
    prompts.py            System prompts (versioned constant PROMPT_VERSION)
  beds/
    models.py             Site, Bed, Hold, Assignment tables
    service.py            find_matches, hold, release_hold, assign, discharge, summary (stable interface)
    importer.py           CSV/XLSX import with column mapping
    seed.py               Invented demo beds
  workflow/
    pipeline.py           Orchestrates ingest -> evaluate -> verify -> tree -> bed hold; updates referral status
    decisions.py          Apply coordinator decision (transactional), drafts, audit
    drafts.py             Deterministic templates: denial explanation, hospital info request
    kipu.py               KipuClient protocol + MockKipuClient (writes DATA_DIR/outbox/kipu/*.json)
    audit.py              append-only audit log helpers
  api/
    referrals.py          /api/referrals routes
    beds.py               /api/beds, /api/assignments routes
    audit.py              /api/audit
criteria/                 Copied from the public upstream repo: admission-criteria.json (+ .md for humans)
eval/run.py               Batch-runs the pipeline over PACKETS_DIR, scores against decisions.json, writes scorecard
tests/                    pytest; fixtures are INVENTED sample packets (never packet text)
docs/                     context/, superpowers/specs|plans/, api-contract.md, modules/*.md, eval-scorecard.md
data/                     (gitignored) uploads/, cache/, outbox/, app.db
```

## 5. Shared data models (`app/schemas.py`)

These are the contract between workstreams. Change them only in the integration step.

```python
RuleKind = Literal["must", "exclude", "document", "conditional", "ask", "note", "eligible"]
RuleSource = Literal["outline", "by_system", "form", "checklist"]

class Rule(BaseModel):            # produced by criteria.py, never by the LLM
    id: str                       # "MOB-3", "INT-I2", "FORM-X4", "DOC-6", "GEN-1"
    source: RuleSource
    section: str                  # "Mobility", "Integumentary (ineligible)", ...
    kind: RuleKind                # by_system eligible items w/o kind -> "eligible"; ineligible -> "exclude"; GEN-* -> "must" (any_of group)
    text: str
    group: str                    # rule group key used for parallel evaluation (section 7.3)
    human_review: bool = False    # OTH-2
    superseded_by: str | None = None   # FORM-M6, FORM-X3, FORM-X6

class Evidence(BaseModel):
    page: int                     # 1-indexed page of the uploaded PDF
    quote: str                    # verbatim excerpt from that page's transcription, <= 300 chars
    verified: bool = False        # set ONLY by verify.py

RuleStatus = Literal["pass", "fail", "unknown", "not_applicable", "needs_human"]
# Status is ALWAYS from the eligibility point of view:
#   must:        pass = requirement met;           fail = requirement clearly not met
#   exclude:     pass = exclusion NOT present;     fail = exclusion present
#   document:    pass = document present;          fail = document absent (redacted fields are NOT absence)
#   conditional: pass = arrangement confirmed or condition absent; unknown = needed but not confirmed; fail = cannot be arranged
#   eligible:    pass = this eligible condition matches the patient; not_applicable otherwise
#   ask/note:    not_applicable (+ answer text)
#   unknown = packet does not say, or needs a date/hour count that is redacted

class RuleResult(BaseModel):
    rule_id: str
    status: RuleStatus
    reasoning: str                # 1-3 plain sentences
    evidence: list[Evidence] = []
    answer: str | None = None     # for kind=ask
    hospital_question: str | None = None   # required when status == unknown and the rule matters
    confidence: Literal["high", "medium", "low"]

class PageMarkdown(BaseModel):
    page: int
    doc_type: str                 # "fax cover", "IHH referral form", "face sheet", "H&P", "PT evaluation", "labs", "medication record", "progress note", "near-blank", "other"
    orientation: Literal["upright", "rotated_90", "rotated_180", "rotated_270", "unknown"]
    legibility: Literal["good", "partial", "poor"]
    markdown: str

class PacketText(BaseModel):
    packet_hash: str              # sha256 of PDF bytes
    page_count: int
    pages: list[PageMarkdown]
    def to_markdown(self) -> str: ...   # "## Page {n} — {doc_type}\n\n{markdown}" joined

class Conflict(BaseModel):
    topic: str                    # "Ambulation distance"
    statements: list[Evidence]    # >= 2 statements that disagree
    related_rule_ids: list[str]
    why_it_matters: str

class BedNeeds(BaseModel):
    sex: Literal["male", "female", "unknown"]
    wheelchair: bool | None
    mobility_aid: str | None      # "walker", "cane", ...
    oxygen: bool | None
    isolation: bool | None
    notes: str | None

class CaseSummary(BaseModel):
    one_liner: str                # "Adult man referred after inpatient stay for left-leg cellulitis; walks with walker."
    age: str | None               # as stated, or None
    sex: Literal["male", "female", "unknown"]
    admitting_diagnosis: str | None
    housing_situation: str | None
    has_ihh_referral_form: bool
    documents_present: list[DocumentPresence] # {doc_id: 'DOC-6', pages: [4, 5]} (a list, not a dict: strict structured outputs)
    key_findings: list[str]
    conflicts: list[Conflict]
    bed_needs: BedNeeds

Decision = Literal["approve", "deny", "needs_review"]

class ReviewReason(BaseModel):
    kind: Literal["missing_info", "judgment_call", "unverified_evidence", "conflict", "no_bed"]
    rule_ids: list[str]
    detail: str

class HospitalQuestion(BaseModel):
    rule_id: str
    question: str

class TreeStep(BaseModel):
    node: Literal["exclusions", "must_rules", "general_eligibility", "human_review", "missing_info", "unverified", "conflicts", "bed"]
    outcome: Literal["triggered", "clear"]
    rule_ids: list[str]
    explanation: str

class BedOption(BaseModel):
    bed_id: str
    site: str
    room: str
    label: str
    match_notes: list[str]        # "wheelchair accessible", "female room"

class Recommendation(BaseModel):
    decision: Decision
    headline: str                 # one sentence for the summary card
    top_reasons: list[str]        # <= 3, each ends with rule IDs in brackets, e.g. "Walks 150 ft with walker (p.12) [MOB-3]"
    review_reasons: list[ReviewReason]
    hospital_questions: list[HospitalQuestion]
    tree: list[TreeStep]
    held_bed: BedOption | None
    alternative_beds: list[BedOption]

class AnalysisResult(BaseModel):  # stored as JSON on the referral row
    prompt_version: str
    model: str
    packet: PacketText
    summary: CaseSummary
    rule_results: list[RuleResult]
    recommendation: Recommendation
    timings: dict[str, float]

ReferralStatus = Literal["uploaded", "transcribing", "analyzing", "ready_for_review", "approved", "denied", "info_requested", "failed"]

class CoordinatorDecision(BaseModel):
    decision: Literal["approve", "deny", "request_info"]
    coordinator: str
    notes: str = ""
    rule_overrides: list["RuleOverride"] = []
    bed_id: str | None = None     # None = use held bed

class RuleOverride(BaseModel):
    rule_id: str
    status: RuleStatus
    note: str
```

## 6. Ingest (PDF → markdown)

1. `render.py`: open the PDF with PyMuPDF, render every page at 150 dpi to PNG (JPEG q85 if PNG > 4 MB). Cache under `DATA_DIR/cache/{hash}/pages/{n}.png`. `render_page(pdf_path, n, rotate=0)` supports re-rendering with rotation.
2. `ocr.py`: for each page, concurrently (semaphore `OCR_CONCURRENCY`, default 8), send the image to Claude with a transcription prompt. The response is a structured `PageMarkdown`.
   - The prompt says: transcribe faithfully, preserve tables as markdown tables, mark black redaction boxes as `[REDACTED]`, mark unreadable text as `[illegible]`, never infer or complete text, report orientation and doc_type.
   - If orientation != upright, re-render with the inverse rotation and transcribe once more, keeping the better legibility.
3. Assemble `PacketText`. Cache it as `DATA_DIR/cache/{hash}/packet.json`. A second upload of the same file skips OCR.
4. A page that fails twice becomes `PageMarkdown(doc_type="other", legibility="poor", markdown="[transcription failed]")`. The run continues and the failure is visible in details.

## 7. Rules engine

### 7.1 Criteria loader
`criteria.py` flattens `criteria/admission-criteria.json` into `list[Rule]`:
- `general.any_of` → GEN-1..3 (kind `must`, source `outline`, group `general`).
- `rules[]` → kind as given (source `outline`).
- `by_system[].eligible[]` → kind `conditional` if given, else `eligible`; `by_system[].ineligible[]` → kind `exclude` (source `by_system`).
- `referral_checklist.items[]` → DOC-1..10, kind `document` (source `checklist`).
- `referral_form_screen.must_meet_all[]` → kind `must`; `not_eligible_if[]` → kind `exclude` (source `form`). Items with `superseded_by` are kept for display but never sent to the LLM; their status is fixed to `not_applicable` with reasoning "Superseded by {ids} (outline wins)".

### 7.2 Case summary
One call over the full packet markdown → `CaseSummary` (bed needs, conflicts, documents present). It runs first so the
packet prefix is written to the prompt cache before the parallel calls.

### 7.3 Rule evaluation (parallel)
Eight rule groups (≤30 rules each), evaluated concurrently. Each gets the same cached prefix (system prompt + packet markdown + precedence
notes + the known-gaps table) followed by its rule list:

| Group | Rules |
|---|---|
| `function` | GEN-*, ADL-*, MOB-*, COG-*, FORM-M1..M5 |
| `behavior` | MH-*, OTH-*, FORM-X1, X2, X5, X7 |
| `infection_docs` | Infection Control, Infection, End of life, FORM-X4, DOC-1..10 |
| `disease` | Diabetes, Cardiac, Respiratory Illness |
| `systems_a` | by_system: Integumentary, Ophthalmology, Respiratory |
| `systems_b` | by_system: Musculoskeletal, GI |
| `systems_c` | by_system: Endocrine, GU |
| `systems_d` | by_system: Circulatory System, Neurology |

The output is `list[RuleResult]`. The engine checks coverage: any rule ID in the group missing from the output gets
`status="unknown", confidence="low", reasoning="Not evaluated by the model"`, which is flagged. Unknown rule IDs
returned by the model are dropped and logged.

Prompt rules (in `prompts.py`, `PROMPT_VERSION` bumped on change):
- The outline wins over the form; the settled conflicts are listed verbatim from `admission-criteria.json.precedence`.
- Check the chart, not only the referral form. When they disagree, cite both.
- Not in the packet → `unknown` plus a `hospital_question`. Hour or date windows → `unknown` ("dates are redacted").
- `[REDACTED]` is never evidence of absence.
- Quotes must be copied verbatim from the transcription of the cited page.
- Sex-offender status or neurosurgery → `needs_human`. Never `fail` on OTH-2.

### 7.4 Quote verification (`verify.py`, deterministic)
For each Evidence, normalize the quote and the cited page markdown (lowercase, collapse whitespace, strip punctuation
except digits/`.`/`%`) and require `rapidfuzz.fuzz.partial_ratio >= 90`. If the cited page doesn't contain it, search ±1
page (an off-by-one cite) and correct `page` if found. Sets `verified`.

### 7.5 Decision tree (`tree.py`, pure function)
`decide(rules, results, summary, bed_options) -> Recommendation`. Evaluated in order, recording a `TreeStep` per node:

1. **exclusions**: `exclude` rules with `status == fail` and at least one verified evidence → triggered.
2. **must_rules**: `must` rules (excluding GEN-*) with `status == fail` and verified evidence → triggered.
3. **general_eligibility**: at least one of GEN-1..3 is `pass`. If all three `fail` with verified evidence → triggered (deny). If none pass but some are unknown → missing_info.
4. **human_review**: any `needs_human`, or any `human_review` rule not `pass`/`not_applicable` → review reason `judgment_call`.
5. **missing_info**: `must`/`exclude`/`conditional` rules with `unknown`, and `document` rules with `fail`/`unknown` → review reason `missing_info`, plus their `hospital_question`s. `ask` answers are shown but never block.
6. **unverified**: any `fail` whose evidence is all unverified → review reason `unverified_evidence` (it never drives a denial).
7. **conflicts**: summary conflicts touching a failed or unknown rule → review reason `conflict`.

**Outcome:** steps 1–3 triggered → `deny`, still listing the review reasons. Otherwise any review reason → `needs_review`.
Otherwise → `approve`.
**Bed node:** for `approve` and `needs_review`, attach the held bed. If no bed matches, an approve stays `approve` with
review reason `no_bed` and the headline says "Eligible, no matching bed available".
`top_reasons`: deny → the triggered rules first; needs_review → the review reasons; approve → the strongest passes
(general eligibility, mobility, ADL, cognition), each with page and rule ID.

## 8. Bed management (`app/beds/`)

Baseline model, to be revised when IHH's sheet details arrive. Only `service.py`'s functions are used by other modules.

Tables:
- `Site(id, name)`
- `Bed(id, site_id, room, label, sex_designation: male|female|any, wheelchair_accessible, ground_floor, oxygen_ok, isolation_ok, status: available|occupied|out_of_service, notes)`. The API status `held` is **derived** from an active, unexpired Hold.
- `Hold(id, bed_id, referral_id, created_at, expires_at, active)`, default TTL `HOLD_TTL_HOURS=4`
- `Assignment(id, bed_id, referral_id, client_label, start_date, expected_end_date, released_at, status: active|released)`. `client_label` is a non-identifying label (e.g. referral ID).

Service interface:
- `find_matches(needs: BedNeeds, limit=3) -> list[BedOption]`. Hard filters: status available (expired holds count as available), sex compatible (`unknown` sex → only `any` beds, with a match note), wheelchair → accessible, oxygen → oxygen_ok, isolation → isolation_ok. Ranking prefers the least specialized bed that fits, so accessible beds are kept for people who need them.
- `hold(bed_id, referral_id) -> Hold`, `release_hold(referral_id)`, `expire_holds()`
- `assign(bed_id, referral_id, client_label, expected_end_date=None) -> Assignment`. Runs in one transaction, re-checks availability, converts the hold, raises `BedUnavailable`.
- `discharge(assignment_id)` frees the bed. `set_status(bed_id, status)` handles out_of_service.
- `summary() -> {total, available, held, occupied, out_of_service, by_site: [...]}`
- `import_beds(file, mapping=None)`: CSV or XLSX, header-based mapping with a default mapping; returns `{created, updated, errors[]}`.

Seed data: two invented sites, about 20 beds with mixed attributes, a few already occupied. No real data.

## 9. Workflow, approval, Kipu mock

`pipeline.run(referral_id)` runs as a FastAPI BackgroundTask: `transcribing` → `analyzing` → `ready_for_review` (or `failed` with an error message). After the tree, if the decision is not `deny` and a bed matches, it places a hold on the top match.

`decisions.apply(referral_id, CoordinatorDecision)`:
- **approve:** apply rule overrides, `beds.assign(bed_id or held bed)`, build `KipuAdmissionDraft`, call `KipuClient.create_admission` (mock writes JSON to `DATA_DIR/outbox/kipu/{referral_id}.json` and returns `MOCK-KIPU-{n}`), status → `approved`. If the bed is unavailable, return 409 with alternatives.
- **deny:** release the hold and generate the denial draft (`drafts.denial(...)`, citing rule IDs and plain reasons, marked DRAFT, never sent). Status → `denied`.
- **request_info:** keep the hold, generate the hospital info request draft from `hospital_questions` + coordinator notes. Status → `info_requested`.
- Every action writes an `AuditEvent(ts, actor, referral_id, action, detail)`. The audit records **agreement**: did the coordinator follow or override the AI recommendation, and which rules were overridden. That is our QC trail.

`KipuAdmissionDraft` holds what we have (referral ID, bed/site/room, admitting diagnosis, summary, rule outcome summary,
approved_by, approved_at). The field mapping to the real Kipu API is explicitly **TBD**: we don't invent Kipu field names.

## 10. API (summary; full contract in `docs/api-contract.md`)

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/referrals` | multipart `file` (PDF) → `202 {id, status}`; starts the pipeline |
| GET | `/api/referrals` | list: id, filename, status, decision, headline, created_at |
| GET | `/api/referrals/{id}` | summary view: status, recommendation (decision, headline, top_reasons, review_reasons, hospital_questions), one_liner, held_bed, alternatives, coordinator decision if any |
| GET | `/api/referrals/{id}/details` | full AnalysisResult with rule text and kind joined into each RuleResult, tree, conflicts, timings |
| GET | `/api/referrals/{id}/pages/{n}` | PageMarkdown |
| GET | `/api/referrals/{id}/pages/{n}/image` | PNG of the page (so the coordinator can check a quote against the scan) |
| POST | `/api/referrals/{id}/decision` | CoordinatorDecision → updated referral; 409 if bed unavailable |
| GET | `/api/referrals/{id}/drafts` | denial / info-request drafts + Kipu payload if any |
| POST | `/api/referrals/{id}/retry` | re-run a `failed` referral (reuses OCR cache) |
| GET | `/api/beds` | list beds with current status/assignment; filters `site`, `status` |
| GET | `/api/beds/summary` | counts |
| POST | `/api/beds` / PATCH `/api/beds/{id}` | create / edit / set status |
| POST | `/api/beds/import` | multipart CSV/XLSX |
| GET | `/api/referrals/{id}/bed-matches` | ranked BedOptions for a referral |
| POST | `/api/assignments/{id}/discharge` | free a bed |
| GET | `/api/audit?referral_id=` | audit events |
| GET | `/api/health` | `{ok, model, api_key_configured}` |

## 11. Error handling

- Missing API key: `/api/health` reports it, and upload returns 503 with a clear message.
- Refusal, rate limit or API error during a stage: retry via SDK (max_retries=3). If it still fails, the referral goes to `failed` with stage and message. The coordinator can `POST /api/referrals/{id}/retry` (reuses the OCR cache).
- Non-PDF or encrypted PDF → 400.
- Malformed model output: `messages.parse` validates. On a validation error, retry once, then mark the group `unknown` (coverage step).
- Bed race: `assign` re-checks inside the transaction → 409.

## 12. Quality control (the event guardrail)

1. Quote verification (7.4). Unverified evidence can't deny.
2. Coverage check (7.3). Every rule has a result, and unevaluated rules are visible.
3. Conflicts list (7.2), surfaced in the summary.
4. Page image endpoint, so a human can check any quote against the scan in one click.
5. Human final decision, with overrides and agreement logged.
6. **Eval harness:** `python -m eval.run --packets-dir ... --decisions .../decisions.json` runs all packets with
   concurrency 2, then writes `docs/eval-scorecard.md` (packet, IHH decision, our decision, match, deciding rule IDs,
   runtime; plus totals, false approvals, false denials, needs_review count) and full JSON to `DATA_DIR/eval/`.

## 13. Testing

- Pure units with no network: criteria loader (counts, superseded handling), verifier, decision tree (table-driven
  cases for each node), bed service (in-memory SQLite: matching, hold expiry, double-assign race), importer, drafts.
- LLM modules: inject a fake async client returning canned parsed objects. Tested on **invented** fixture packets (`tests/fixtures/`).
- API: FastAPI TestClient with the pipeline stubbed.
- One opt-in live smoke test (`pytest -m live`) that runs a tiny invented 1-page PDF through Claude. It needs a key.

## 14. Workstreams

| WS | Owner | Depends on | Delivers |
|---|---|---|---|
| WS0 | lead | — | scaffold, `schemas.py`, `config.py`, `db.py`, `llm.py`, criteria copy, `docs/api-contract.md`, CLAUDE.md |
| WS1 | agent | WS0 | `ingest/` + tests + `docs/modules/ingest.md` |
| WS2 | agent | WS0 | `rules/` + tests + `docs/modules/rules-engine.md` |
| WS3 | agent | WS0 | `beds/` + `api/beds.py` + tests + `docs/modules/bed-management.md` |
| WS4 | agent | WS0 (stubs WS1-3) | `workflow/`, `api/referrals.py`, `api/audit.py` + tests + `docs/modules/workflow.md` |
| WS5 | lead + agent | WS1-4 | integration, `eval/run.py`, live run over 11 packets, prompt tuning, scorecard |
| WS6 | lead | all | README per upstream template, demo script, recording |

Cut order if behind: (1) attribute matching → availability only; (2) Kipu payload → plain JSON dump;
(3) orientation retry; (4) parallel groups → a single rules call.

## 15. Open items

- BMS details from IHH (columns of their Excel sheet, sites, room rules). Only `beds/` changes when they arrive.
- Real Kipu field mapping: post-event.
- `needs_review` → re-run with an addendum PDF (hospital sends more pages): stretch goal, endpoint `POST /api/referrals/{id}/addendum`.
