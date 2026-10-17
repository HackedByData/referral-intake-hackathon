# Census-backed, county-aware BMS: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use `- [ ]`.

**Goal:** IHH's per-facility census lives in the BMS (facilities, beds, statuses, stays with a patient-info section),
seeded with POC placebo data. Referrals match only recuperative-care facilities in the patient's county, which the AI
suggests from cited evidence and the coordinator confirms.

**Spec:** [`docs/superpowers/specs/2026-10-17-bms-census-county-design.md`](../specs/2026-10-17-bms-census-county-design.md)

**Base:** commit `59278d1` (Step 0 done: schemas, `Site`/`Assignment` fields, the county/program filter in `find_matches`,
updated fixtures; 143 tests pass).

## Global Constraints
- Every Global Constraint of `docs/superpowers/plans/2026-10-17-referral-intake.md` still applies: naive UTC +
  `NaiveDatetime` table fields, no network in tests, invented test text only, never commit `*.xlsx` from IHH /
  `data/` / `.env`.
- The census workbook (`C:/Users/devin/UCI/Claude Hackathon/Test Census.xlsx`) is confirmed synthetic. Agents MAY
  read it to build and verify the parser. It must **never** be copied into the repo. Committed artifacts derived from
  it contain structure only (facility names, bed labels, statuses), never its people data.
- Shared types are in `app/schemas.py` (frozen in Step 0). Do not rename existing fields. Additions need a note in
  your report.
- The DB schema changed. Delete `data/app.db` before running the server locally.

## Review Focus
- Re-importing the same workbook twice creates no duplicate beds or stays.
- A census re-import never closes or overwrites a stay created by an approved referral (`source="referral"`).
- A referral with no determinable county never gets a bed held, and says why (`county_needed`).
- Confirming a county twice (or changing it) releases the old hold and holds a bed in the new county.
- Side-by-side blocks (SFS, Lincoln Park, MWC, Broadway, RVS, Laguna Hills, Anaheim) all import their beds.

---

### Task A: Census parser/importer, POC seed, facilities and bed-detail API (workstream A)

**Files**
- Create: `app/beds/census.py`, `tools/build_poc_seed.py`, `app/beds/seed_poc.json` (generated), `tests/test_census.py`
- Modify: `app/beds/service.py` (to_view stay summary, bed detail, facilities), `app/beds/seed.py` (`seed_poc_beds`),
  `app/api/beds.py` (new routes), `app/main.py` (seed mode), `app/config.py` (`seed_mode`), `docs/modules/bed-management.md`
- Tests touched: `tests/test_api_beds.py`, `tests/test_api_referrals.py` (set `SEED_MODE=demo` in their fixtures)

**Interfaces produced**
- `census.parse_workbook(data: bytes) -> tuple[list[ParsedSheet], list[str]]`, which returns (sheets, skipped sheet names with reasons).
  `ParsedSheet(sheet: str, rows: list[ParsedRow], skipped: list[str])`.
  `ParsedRow(label: str, status: str, bed_notes: str, wheelchair: bool, sex_designation: str, stay: dict | None)`.
  `stay` keys: `client_id, first_name, last_name, gender, intake_date, expected_exit_date, exit_date, payer,
  case_manager, notes, extra` (dict of every other non-empty column, verbatim header → str value).
- `census.import_census(session, sheets, program_hint=True) -> CensusImportReport`
- `service.facility_view / list_facilities(session) -> list[FacilityView]`, `update_facility(session, id, FacilityUpdate)`,
  `bed_detail(session, bed_id) -> BedDetail`. `to_view` fills `stay: StaySummary` for occupied beds.
- `seed.seed_poc_beds(session) -> int` loads `seed_poc.json` on an empty DB.
- Routes: `GET /api/facilities`, `PATCH /api/facilities/{id}` (404), `GET /api/beds/{bed_id}` (404),
  `POST /api/census/import` (multipart .xlsx → `CensusImportReport`, 400 for non-xlsx).

**Parser rules (from the spec; the agent verifies them against the real workbook):**
- Header row = the first of rows 1–3 with a cell matching `^\s*I[FE]\s*/?\s*(I[FE])?\s*#`.
- Block starts:
  - a header matching `^(bed|room|rm)\b` (case-insensitive, after stripping) at column j; or
  - column j directly before an IF column, when header[j] is empty or has no letters (`` ` ``, `///`, `/`).
  
  A block ends at the next block start.
- Header synonyms (case-insensitive, trailing `:` and `?` and extra spaces stripped):

  | Header(s) | Field |
  |---|---|
  | `IF#`, `IF #`, `IF/IE#` | client_id |
  | `FIRST`, `First Name`, `Name` | first_name |
  | `LAST`, `Last Name`, `Last`, `Last Int.` | last_name |
  | `GENDER` | gender |
  | `INTAKE DATE`, `Intake Date`, `Admission`, `Admit Date`, `Intake` | intake_date |
  | `Anticipated Discharge Date` | expected_exit_date |
  | `Exit Date`, `Exit` | exit_date |
  | `Contract`, `Insurance`, `Referring Agency`, `Referrer` | payer (first non-empty wins) |
  | `CASE MANAGER` | case_manager |
  | `Notes` | notes |

  Buena Park RC has two `First Name:` headers: the second becomes last_name. Everything else goes to `extra`.
  Headers that are summary counters (contain `:` plus a digit, or start with `Current`, `Intakes Today`,
  `Female Beds`, `Male Beds`, `Total`) are ignored.
- Dates: datetime → date. Strings `TBD`/`TBH`/empty → None. Other strings → try `%m/%d/%Y`, else None (keep the raw
  value in `extra`).
- Status words and skipped labels follow the spec table. ADA in the label means wheelchair. A label matching
  `^M\d` gives sex `male`, and `^W\d` gives `female`; otherwise `any`.
- Sheets skipped: no header row found, or no rows with a label. Name them in `skipped_sheets` with a reason.

**Import semantics**
- Facility id = `slug(sheet name)`. Create it if missing, with program_type from an explicit name hint only
  (`RC`/`Recup` → recuperative_care, `PRK` → project_roomkey, `SHELTER` → shelter, else unknown) and county None.
  **Never overwrite** county or program_type on an existing facility.
- Bed id = `slug(f"{facility_id}-{label}")`. Upsert room=label and label. Status only from the parser.
- Stays: for each bed, close active `source="census"` stays that differ from the parsed stay (same `client_id` and
  `first_name` = same stay, which gets updated in place). Create a new census stay if parsed. If a bed has an active
  `source="referral"` stay, leave it and the bed status untouched, and add a conflict line when the sheet shows a
  different occupant.

**POC seed** (`tools/build_poc_seed.py "<path to Test Census.xlsx>"` → writes `app/beds/seed_poc.json`)
- Uses `parse_workbook` for the structure, then REPLACES every stay with placebo data:
  - `client_id`: `POC-0001`…
  - name: `Test` / `Client 001`…
  - `gender`: random M/F (seeded `random.Random(42)`)
  - `payer`: cycles through `["Cal Optima", "LA Care", "DHS", "Health Net", "Molina", "Anthem", "Blue Shield", "Kaiser"]`
  - `intake_date`: today minus 1–60 days; `expected_exit_date`: intake + 30 days
  - `case_manager`: `CM Placeholder N`
  - `notes`: `POC placebo occupant`
  - `extra`: `{"PETS": "No", "VI-SPDAT": "N/A", "Phone": "000-000-0000"}`
- Copies NOTHING person-related from the workbook.
- Placeholder facility attributes. Every facility gets `notes = "POC placeholder county/program — confirm with IHH"`:

  | County / program | Facilities |
  |---|---|
  | Los Angeles · recuperative_care | RC Whittier 2.0, SFS, Lincoln Park, Broadway |
  | Los Angeles · project_roomkey | Royal Pagoda PRK, LONG BEACH PRK |
  | Los Angeles · shelter | ALO |
  | Orange · recuperative_care | RC Midway, Midway Recup, Buena ParkRC FULL, MWC, Coral; Midway - Closed (active=false) |
  | Orange · shelter | BP EMERGENCY SHELTER QUARANTINE, Laguna Hills VPS, Anaheim VPS, Stanton VPS, Joplin |
  | Riverside · recuperative_care | RVS |
- `seed_poc.json` = `{"generated": "...", "facilities": [{id, name, county, program_type, active, notes,
  beds: [{label, status, notes, wheelchair, sex_designation, stay|null}]}]}`. Commit it.
- `config.Settings.seed_mode` = env `SEED_MODE` (`poc` default | `demo` | `none`). The lifespan calls
  `seed_poc_beds` / `seed_demo_beds` accordingly.

**Tests (`tests/test_census.py`, invented workbook built with openpyxl in a fixture)**
- One fixture workbook with 4 sheets:
  - `RC Test` (single block, header row 1, IF#/FIRST/LAST/GENDER/Contract/INTAKE DATE/PETS);
  - `Twin Test` (two side-by-side blocks, header row 2, title row 1);
  - `Junk Test` (block start header `///`);
  - `Empty`.
  Include rows for: occupied, Open, Needs Cleaning, Closed, `Staff` (skipped), `101A (ADA)`, `M1`/`W1`, a summary
  label `Total Client`, and a `TBD` exit date.
- Assert: parse counts per sheet; statuses; skipped labels listed; ADA → wheelchair; M/W → sex; `extra` holds PETS;
  dates parsed; `Empty` in skipped_sheets.
- `import_census` twice → identical bed and stay counts (idempotent); changed occupant → old stay closed, new created;
  a referral stay is preserved and a conflict is reported; an existing facility's county is not overwritten.
- API: `/api/census/import` with the fixture (200 + report), non-xlsx → 400; `GET /api/facilities`; PATCH county →
  matching now works for that facility; `GET /api/beds/{id}` returns `current_stay.extra`; 404s.
- `seed_poc_beds` on an empty DB loads ≥ 15 facilities and ≥ 600 beds from the committed JSON, and every stay's
  `client_id` starts with `POC-`. A test asserts no name in the JSON other than `Test`/`Client NNN`.
- Optional `@pytest.mark.skipif(not Path(<workbook>).exists())` test: parse the real workbook → ≥ 18 sheets with rows,
  ≥ 600 beds, and SFS/Lincoln Park each yield beds from both blocks.

---

### Task B: Patient county (AI extracts, code maps, coordinator confirms) (workstream B)

**Files**
- Create: `criteria/plan_county.json`, `app/rules/county.py`, `tests/test_county.py`
- Modify: `app/rules/prompts.py` (SUMMARY_TASK bullets + bump `PROMPT_VERSION`), `app/rules/verify.py`
  (`verify_summary` also verifies `health_plan_evidence` and `location_clues`), `app/rules/tree.py` (bed node:
  `county_needed`), `app/workflow/pipeline.py` (set the suggestion before matching), `app/api/referrals.py`
  (`POST /{id}/county`, `ReferralView.county`), `tests/test_pipeline.py`, `tests/test_tree.py`,
  `tests/test_api_referrals.py`, `docs/modules/rules-engine.md`, `docs/modules/workflow.md`

**`criteria/plan_county.json`** (comment key `_about` says it is editable and lists only single-county Medi-Cal plans)
```json
{"_about": "Editable. Maps a health plan named in a referral to the county it serves. Only plans that serve exactly one county give a suggestion; multi-county plans list several or none. Coordinators always confirm.",
 "plans": {"cal optima": ["Orange"], "caloptima": ["Orange"], "la care": ["Los Angeles"], "l a care": ["Los Angeles"],
           "iehp": ["Riverside", "San Bernardino"], "inland empire health plan": ["Riverside", "San Bernardino"],
           "health net": [], "molina": [], "anthem": [], "blue shield": [], "blue cross": [], "kaiser": []}}
```
(Keys are matched against `normalize(health_plan)` =
lowercase, non-alphanumerics → space, collapsed. A key matches when it appears as a whole-word substring.)

**`county.suggest_county(summary: CaseSummary, plan_map: dict | None = None) -> CountySuggestion`**
- No `health_plan` → `county=None`, `basis="No health plan named in the packet."` plus clue quotes appended
  (`"; location clues: '<quote>' (p.N)"`). Never infer a county from clues.
- Plan matches a key with exactly 1 county → `county=that`, `candidates=[that]`,
  `basis=f"Health plan '{plan}' (p.{pages}) serves {county} County"`, adding `" — quote not verified"` if no
  plan evidence is verified.
- A key with >1 → `county=None`, `candidates=[...]`, `basis=f"Health plan '{plan}' serves several counties: ..."`.
- A key with 0, or no key → `county=None`, `basis=f"Health plan '{plan}' does not map to a single county"`.
- `load_plan_map()` reads `criteria/plan_county.json` (lru_cache).

**SUMMARY_TASK additions:**
- `health_plan`: the health plan or payer named in the packet, exactly as written (e.g. on an authorization form or face sheet), with verbatim quotes in `health_plan_evidence`. Null if none.
- `location_clues`: verbatim quotes naming a city or county where the patient lives or is hospitalized. Never use [REDACTED] text.
- Leave `bed_needs.county` and `suggested_county` null.

**Pipeline** (after `verify_summary`):
```python
suggestion = suggest_county(summary)
summary = summary.model_copy(update={"suggested_county": suggestion,
    "bed_needs": summary.bed_needs.model_copy(update={"county": suggestion.county})})
```
Then the existing `find_matches` / `decide` / hold.

**Tree bed node:** when the decision is not deny and there are no options:
- if `summary.bed_needs.county` is None → `ReviewReason(kind="county_needed", rule_ids=[], detail="Patient county not confirmed; confirm the county to match a bed.")`, bed step explanation "County needed to match a bed.", approve headline `"Recommend approve: eligible; confirm county to hold a bed"`;
- else the existing `no_bed`.

**`POST /api/referrals/{id}/county`** (`CountyConfirm`) → `ReferralView`
- 404 if unknown; 409 unless status is `ready_for_review` or `info_requested`.
- Update `summary.suggested_county` (county, `confirmed=True`, `confirmed_by`, `basis = "Confirmed by {coordinator}" + ("; AI suggestion: " + old basis)`) and `bed_needs.county`.
- `beds.release_hold`, then `find_matches`, then `rec = decide(criteria.rules, result.rule_results, summary, options)`.
- If `rec.held_bed`, `beds.hold` (on BedUnavailable/BedNotFound, clear it and add `no_bed`, same as the pipeline).
- Save `result_json`; `audit.record("county_confirmed", actor=coordinator, county=..., held_bed=...)`.
- `_view` sets `county=summary.suggested_county`.

**Tests**
- `test_county.py`: CalOptima/"Cal Optima"/"CALOPTIMA" → Orange; "L.A. Care Health Plan" → Los Angeles; IEHP → None with 2 candidates; Health Net → None; no plan → None with clue quotes in basis; an unverified plan quote → the basis says not verified.
- `test_tree.py`: no options + county None → `county_needed` (not `no_bed`), approve headline; no options + county set → `no_bed`.
- `test_pipeline.py`: the fake summary with `health_plan="CalOptima"` and verified evidence → held bed in the Orange site; with no plan → no hold and `county_needed`. Update the existing tests that expect a held bed to supply a CalOptima plan.
- `test_api_referrals.py`:
  - a referral analyzed with no county → `POST county {"county":"Orange"}` → held bed set, no `county_needed`, audit has `county_confirmed`;
  - changing to another county releases the old hold;
  - 409 after approval; 404.
- `verify_summary` verifies `health_plan_evidence`.
