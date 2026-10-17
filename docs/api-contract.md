# API contract (frontend ↔ backend)

**Status:** v3 (2026-10-17), additive over v1/v2. Backend field names match `app/schemas.py` exactly.
Changes will be announced and added to the changelog at the bottom.

Base URL (local): `http://localhost:8000`. Interactive docs once the server runs: `http://localhost:8000/docs`.
CORS is open. All bodies are JSON unless noted. Times are UTC ISO-8601 without a timezone suffix
(e.g. `"2026-10-17T18:02:11"`). `null` means "not known / not applicable"; the UI should show "Unknown", never a guess.

---

## 1. Coordinator flow (what screens call what)

1. **Upload.** `POST /api/referrals` (multipart form, field name `file`, a PDF) → `202 ReferralView` with `status: "uploaded"`.
2. **Processing.** Poll `GET /api/referrals/{id}` every 2 s while `status` is `uploaded | transcribing | analyzing`.
   Show `progress` (e.g. `"Transcribing page 12 of 34"`). This takes about 2–5 minutes for a 30-page packet.
   If `status: "failed"`, show `error` and a **Retry** button → `POST /api/referrals/{id}/retry`.
3. **Summary card** (`status: "ready_for_review"`). Everything comes from `recommendation`:
   - `decision`: `approve | deny | needs_review`, the AI's *recommendation* (badge)
   - `headline`: one sentence
   - `top_reasons[]`: ≤3 strings, each ending in rule IDs, e.g. `"Walks 150 ft with walker (p.12) [MOB-3]"`
   - `held_bed`: the bed already reserved for this patient (`null` if none matched; then show `review_reasons` of kind `no_bed`)
   - `review_reasons[]` and `hospital_questions[]`: shown for `needs_review` (the "call the hospital" list)
   - `one_liner` (top level of `ReferralView`): a short, de-identified patient description
4. **"Show AI reasoning" button** → `GET /api/referrals/{id}/details`:
   - `recommendation.tree[]`: the decision path, in order (render as a vertical stepper/tree)
   - `rule_results[]`: every rule with status, reasoning and evidence (group by `section`, filter by `status`)
   - `summary.conflicts[]`: places where pages of the packet disagree
   - `summary.key_findings[]`, `summary.documents_present[]`
   - Evidence click: `GET /api/referrals/{id}/pages/{page}/image` (PNG of the scanned page, already rotated upright)
     and `GET /api/referrals/{id}/pages/{page}` (the transcription). **Style `verified: false` evidence as a
     warning**: the quote could not be found on that page.
5. **Coordinator decides** → `POST /api/referrals/{id}/decision` with a `CoordinatorDecision`.
   - Approve with the held bed: `{"decision": "approve", "coordinator": "Name"}`
   - Approve with a different bed: add `"bed_id"` from `GET /api/referrals/{id}/bed-matches`
   - `409` → bed no longer available, or already decided. Show `detail`, refresh bed matches.
6. **After deciding** → `GET /api/referrals/{id}/drafts`: denial letter draft, hospital info-request draft, and the
   (mock) Kipu payload. Drafts are **never sent** by the system. Show a "Copy" button.
7. **Bed board** → `GET /api/beds`, `GET /api/beds/summary`; discharge with `POST /api/assignments/{id}/discharge`;
   import the Excel sheet with `POST /api/beds/import`.

The coordinator always makes the final call. The AI decision is a recommendation, and the UI should say so.

---

## 2. Endpoints

| Method | Path | Body / query | Returns |
|---|---|---|---|
| GET | `/api/health` | | `{ "ok": true, "model": "claude-opus-5-5", "api_key_configured": true }` |
| POST | `/api/referrals` | multipart `file` (PDF) | `202 ReferralView` · `400` not a readable PDF · `503` server has no API key |
| GET | `/api/referrals` | | `ReferralListItem[]`, newest first |
| GET | `/api/referrals/{id}` | | `ReferralView` · `404` |
| GET | `/api/referrals/{id}/details` | | `ReferralDetails` · `409` if not analyzed yet |
| GET | `/api/referrals/{id}/pages/{page}` | | `PageMarkdown` · `404` |
| GET | `/api/referrals/{id}/pages/{page}/image` | | `image/png` (or `image/jpeg`) |
| GET | `/api/referrals/{id}/bed-matches` | | `BedOption[]` (best first; includes this referral's held bed) |
| POST | `/api/referrals/{id}/decision` | `CoordinatorDecision` | `ReferralView` · `404` · `409` |
| GET | `/api/referrals/{id}/drafts` | | `Drafts` |
| POST | `/api/referrals/{id}/retry` | | `202 ReferralView` · `409` unless `status` is `failed` |
| GET | `/api/beds` | `?site=&status=` | `BedView[]` |
| GET | `/api/beds/summary` | | `BedSummary` |
| POST | `/api/beds` | `BedCreate` | `201 BedView` |
| PATCH | `/api/beds/{bed_id}` | `BedUpdate` | `BedView` · `404` |
| POST | `/api/beds/import` | multipart `file` (.csv or .xlsx) | `BedImportReport` |
| GET | `/api/beds/{bed_id}` | | `BedDetail` (bed + current stay with patient info + history) · `404` |
| GET | `/api/facilities` | | `FacilityView[]` |
| PATCH | `/api/facilities/{id}` | `FacilityUpdate` | `FacilityView` · `404` |
| POST | `/api/census/import` | multipart `file` (IHH census .xlsx) | `CensusImportReport` · `400` · `413` (over 20 MB) |
| POST | `/api/referrals/{id}/county` | `CountyConfirm` | `ReferralView` · `404` · `409` · `422` (empty county/coordinator) |
| GET | `/api/assignments` | `?active=true` (default) | `AssignmentView[]` |
| POST | `/api/assignments/{id}/discharge` | | `AssignmentView` · `404` |
| GET | `/api/knowledge` | `?status=&kind=` | `LessonView[]` (oldest first) |
| GET | `/api/knowledge/export` | | `{ exported_at, count, lessons: LessonView[] }` |
| POST | `/api/knowledge/{id}/approve` | `LessonReview` | `LessonView` · `404` · `409` unless `proposed` |
| POST | `/api/knowledge/{id}/reject` | `LessonReview` | `LessonView` · `404` · `409` unless `proposed` |
| POST | `/api/knowledge/{id}/retire` | `LessonReview` | `LessonView` · `404` · `409` unless `approved` |
| GET | `/api/audit` | `?referral_id=` | `AuditEventView[]` (oldest first) |

Errors: `{ "detail": "human-readable message" }` with the status codes above.

---

## 3. Types

Notation: TypeScript-style. `?` = may be `null`. String unions are exact values.

```ts
// ---------- Vocabularies ----------
type ReferralStatus = "uploaded" | "transcribing" | "analyzing" | "ready_for_review"
                    | "approved" | "denied" | "info_requested" | "failed";
type Decision = "approve" | "deny" | "needs_review";          // AI recommendation
type CoordinatorChoice = "approve" | "deny" | "request_info";  // human decision
type RuleStatus = "pass" | "fail" | "unknown" | "not_applicable" | "needs_human";
//   always from the ELIGIBILITY point of view: "pass" = good for admission, "fail" = blocks admission
type RuleKind = "must" | "exclude" | "document" | "conditional" | "ask" | "note" | "eligible";
type RuleSource = "outline" | "by_system" | "form" | "checklist";
type Confidence = "high" | "medium" | "low";
type BedStatus = "available" | "held" | "occupied" | "needs_cleaning" | "out_of_service";
type ProgramType = "recuperative_care" | "project_roomkey" | "shelter" | "other" | "unknown";
type SexDesignation = "male" | "female" | "any";

// ---------- Referral ----------
interface ReferralListItem {
  id: string;                 // e.g. "R-7F3A2C"
  filename: string;
  status: ReferralStatus;
  decision?: Decision;        // AI recommendation once analyzed
  headline?: string;
  created_at: string;
}

interface ReferralView extends ReferralListItem {
  progress?: string;          // "Transcribing page 12 of 34"
  error?: string;             // when status == "failed"
  one_liner?: string;
  recommendation?: Recommendation;
  coordinator_decision?: CoordinatorDecisionRecord;
  county?: CountySuggestion;              // v2: county used for bed matching
}

interface CountySuggestion {             // v2, system-filled
  county?: string;                       // null = unknown → no bed held, review reason "county_needed"
  candidates: string[];                  // e.g. ["Riverside","San Bernardino"] for a multi-county plan
  basis: string;                         // e.g. "Health plan 'CalOptima' (p.3) serves Orange County"
  confirmed: boolean;                    // true once a coordinator confirmed/overrode it
  confirmed_by?: string;
  ai_county?: string;                    // v3: the system suggestion before any confirm (kept across confirms)
}

interface CountyConfirm { county: string; coordinator: string; }   // v2

interface Recommendation {
  decision: Decision;
  headline: string;
  top_reasons: string[];                 // ≤3
  review_reasons: ReviewReason[];
  hospital_questions: HospitalQuestion[];
  tree: TreeStep[];                      // decision path, in evaluation order
  held_bed?: BedOption;
  alternative_beds: BedOption[];
  precedents: Precedent[];               // v3: similar past cases where staff-approved lessons say the coordinator differed from the AI
}

interface ReviewReason {
  kind: "missing_info" | "judgment_call" | "unverified_evidence" | "conflict" | "no_bed" | "county_needed";
  rule_ids: string[];
  detail: string;
}

interface HospitalQuestion { rule_id: string; question: string; }

interface TreeStep {
  node: "exclusions" | "must_rules" | "general_eligibility" | "human_review"
      | "missing_info" | "unverified" | "conflicts" | "bed"
      | "knowledge";                     // v3: always last; "triggered" = approved lessons were used (explanation lists #ids)
  outcome: "triggered" | "clear";        // triggered = this step found something
  rule_ids: string[];
  explanation: string;
}

interface ReferralDetails {
  id: string;
  status: ReferralStatus;
  prompt_version: string;
  model: string;
  summary: CaseSummary;
  rule_results: RuleResultView[];
  recommendation: Recommendation;
  pages: PageInfo[];
  timings: Record<string, number>;       // seconds: transcribe, summary, rules, total
  knowledge_used: number[];              // v3: ids of approved lessons that influenced this analysis
}

interface RuleResultView {
  rule_id: string;                       // "MOB-3"
  status: RuleStatus;
  reasoning: string;                     // 1-3 plain sentences
  evidence: Evidence[];
  answer?: string;                       // only for kind "ask"
  hospital_question?: string;            // when status "unknown"
  confidence: Confidence;
  kind: RuleKind;
  section: string;                       // "Mobility", "Integumentary (ineligible)", ...
  source: RuleSource;
  text: string;                          // IHH's rule wording
  superseded_by?: string;                // e.g. "MOB-3, MOB-4" (form rule overridden by internal criteria)
  human_review: boolean;
}

interface Evidence {
  page: number;                          // 1-indexed page of the PDF
  quote: string;                         // verbatim from the transcription
  verified: boolean;                     // false = could not find the quote on that page → warn
}

interface CaseSummary {
  one_liner: string;
  age?: string;
  sex: "male" | "female" | "unknown";
  admitting_diagnosis?: string;
  housing_situation?: string;
  has_ihh_referral_form: boolean;
  documents_present: { doc_id: string; pages: number[] }[];   // DOC-1..DOC-10 checklist
  key_findings: string[];                                     // each ends with "(p.N)"
  conflicts: Conflict[];
  bed_needs: BedNeeds;
}

interface Conflict {
  topic: string;
  statements: Evidence[];                // ≥2 statements that disagree
  related_rule_ids: string[];
  why_it_matters: string;
}

interface BedNeeds {
  sex: "male" | "female" | "unknown";
  wheelchair?: boolean;
  mobility_aid?: string;
  oxygen?: boolean;
  isolation?: boolean;
  notes?: string;
}

interface PageInfo {
  page: number;
  doc_type: string;                      // "H&P", "PT evaluation", "IHH referral form", ...
  orientation: "upright" | "rotated_90" | "rotated_180" | "rotated_270" | "unknown";  // as received
  legibility: "good" | "partial" | "poor";
}

interface PageMarkdown extends PageInfo { markdown: string; }  // [REDACTED] = privacy redaction, not missing

// ---------- Coordinator decision ----------
interface CoordinatorDecision {
  decision: CoordinatorChoice;
  coordinator: string;                   // free-text name
  notes?: string;                        // default ""
  rule_overrides?: { rule_id: string; status: RuleStatus; note: string }[];   // default []
  bed_id?: string;                       // approve only; null = use held bed
}

interface CoordinatorDecisionRecord extends CoordinatorDecision {
  decided_at: string;
  ai_recommendation: Decision;
  agreed_with_ai: boolean;
}

interface Drafts {
  denial?: string;                       // plain text, starts with "DRAFT"
  info_request?: string;                 // plain text, starts with "DRAFT"
  kipu_payload?: object;                 // MOCK — shown for transparency, never sent
  kipu_reference?: string;               // "MOCK-KIPU-R-7F3A2C"
}

// ---------- Beds ----------
interface BedOption {
  bed_id: string;
  site: string;
  room: string;
  label: string;                         // bed letter within the room
  match_notes: string[];                 // ["male room", "wheelchair accessible", "ground floor"]
}

interface BedView {
  id: string;
  site: string;
  room: string;
  label: string;
  sex_designation: SexDesignation;
  wheelchair_accessible: boolean;
  ground_floor: boolean;
  oxygen_ok: boolean;
  isolation_ok: boolean;
  status: BedStatus;                     // "held" = reserved for a referral under review
  notes: string;
  referral_id?: string;                  // holder or occupant
  assignment_id?: number;                // when occupied via the system (use for discharge)
  hold_expires_at?: string;
  site_id: string;                       // v2
  county?: string;                       // v2 (facility county)
  program_type: ProgramType;             // v2
  stay?: StaySummary;                    // v2: current occupant summary
}

interface StaySummary {                  // v2
  assignment_id: number; source: "referral" | "census" | "seed"; referral_id?: string;
  client_id?: string; first_name?: string; last_name?: string; gender?: string;
  intake_date?: string; expected_exit_date?: string; payer?: string;
}

interface StayDetail extends StaySummary { // v2: the "patient info" section of a bed
  status: "active" | "released"; exit_date?: string; case_manager?: string; notes: string;
  extra: Record<string, unknown>;        // every other census column, verbatim (pets, ICMS, ECM, VI-SPDAT, phone, ...)
  released_at?: string;
}

interface BedDetail { bed: BedView; current_stay?: StayDetail; history: StaySummary[]; }   // v2

interface FacilityView {                 // v2: one per census sheet
  id: string; name: string; county?: string; program_type: ProgramType;
  address: string; phone: string; notes: string; active: boolean;
  total_beds: number; available_beds: number;
}

interface FacilityUpdate {               // v2: all optional; send county: null to clear county; other fields ignore null
  county?: string; program_type?: ProgramType; address?: string; phone?: string; notes?: string; active?: boolean;
}

interface CensusImportReport {           // v2
  facilities_created: number; skipped_sheets: string[];
  sheets: { sheet: string; facility_id?: string; beds_created: number; beds_updated: number;
            stays_created: number; stays_closed: number; skipped: string[]; conflicts: string[] }[];
}

interface BedCreate {
  site: string; room: string; label: string; id?: string;
  sex_designation?: SexDesignation; wheelchair_accessible?: boolean; ground_floor?: boolean;
  oxygen_ok?: boolean; isolation_ok?: boolean; notes?: string;
}

interface BedUpdate {                    // all optional; only sent fields change
  sex_designation?: SexDesignation; wheelchair_accessible?: boolean; ground_floor?: boolean;
  oxygen_ok?: boolean; isolation_ok?: boolean; status?: "available" | "needs_cleaning" | "out_of_service"; notes?: string;
}

interface BedSummary {
  total: number; available: number; held: number; occupied: number; out_of_service: number;
  needs_cleaning: number;
  by_site: { site: string; total: number; available: number }[];
}

interface AssignmentView {
  id: number; bed_id: string; referral_id?: string;   // null for census/seed stays
  client_label: string;
  start_date: string;                    // "2026-10-17"
  expected_end_date?: string;
  released_at?: string;
  status: "active" | "released";
}

interface BedImportReport { created: number; updated: number; errors: string[]; }

// ---------- Audit ----------
interface AuditEventView {
  id: number; ts: string; actor: string; referral_id?: string;
  action: string;                        // "uploaded" | "analysis_complete" | "analysis_failed" | "retry" | "coordinator_decision"
  detail: Record<string, unknown>;
}
```

---

## 4. Example `GET /api/referrals/{id}` (invented data)

```json
{
  "id": "R-7F3A2C",
  "filename": "packet.pdf",
  "status": "ready_for_review",
  "decision": "needs_review",
  "headline": "Needs review: 2 item(s) to resolve before deciding",
  "created_at": "2026-10-17T18:02:11",
  "progress": null,
  "error": null,
  "one_liner": "Adult man referred after inpatient stay for left-leg cellulitis; walks with a walker.",
  "recommendation": {
    "decision": "needs_review",
    "headline": "Needs review: 2 item(s) to resolve before deciding",
    "top_reasons": [
      "MRSA swab result is not in the packet [INF-1]",
      "Home health for IV antibiotics is not confirmed [OTH-3]"
    ],
    "review_reasons": [
      {"kind": "missing_info", "rule_ids": ["INF-1"], "detail": "MRSA swab result is not in the packet"},
      {"kind": "missing_info", "rule_ids": ["OTH-3"], "detail": "Home health for IV antibiotics is not confirmed"}
    ],
    "hospital_questions": [
      {"rule_id": "INF-1", "question": "Can you send the MRSA nares swab result?"},
      {"rule_id": "OTH-3", "question": "Has home health been arranged to maintain the IV and give the antibiotic?"}
    ],
    "tree": [
      {"node": "exclusions", "outcome": "clear", "rule_ids": [], "explanation": "No exclusion found with verified evidence."},
      {"node": "must_rules", "outcome": "clear", "rule_ids": [], "explanation": "No required criterion clearly unmet."},
      {"node": "general_eligibility", "outcome": "clear", "rule_ids": [], "explanation": "Meets at least one general admission criterion."},
      {"node": "human_review", "outcome": "clear", "rule_ids": [], "explanation": "No case-by-case judgment needed."},
      {"node": "missing_info", "outcome": "triggered", "rule_ids": ["INF-1", "OTH-3"], "explanation": "Information needed to decide is missing from the packet."},
      {"node": "unverified", "outcome": "clear", "rule_ids": [], "explanation": "All findings are backed by verified quotes."},
      {"node": "conflicts", "outcome": "clear", "rule_ids": [], "explanation": "No contradictions affecting the decision."},
      {"node": "bed", "outcome": "clear", "rule_ids": [], "explanation": "Held bed demo-site-a-102-a."}
    ],
    "held_bed": {"bed_id": "demo-site-a-102-a", "site": "Demo Site A", "room": "102", "label": "A", "match_notes": ["male room"]},
    "alternative_beds": [
      {"bed_id": "demo-site-b-1-a", "site": "Demo Site B", "room": "1", "label": "A", "match_notes": ["male room", "ground floor"]}
    ]
  },
  "coordinator_decision": null
}
```

## 5. Example `rule_results[]` item from `/details` (invented)

```json
{
  "rule_id": "MOB-3", "status": "pass", "confidence": "high",
  "reasoning": "PT note says the patient walked 150 ft with a walker without help.",
  "evidence": [{"page": 12, "quote": "Patient ambulated 150 ft with front wheeled walker, modified independent", "verified": true}],
  "answer": null, "hospital_question": null,
  "kind": "must", "section": "Mobility", "source": "outline",
  "text": "Patient must be able to ambulate at least 100 ft (rest breaks are acceptable) at modified independence if not wheelchair dependent",
  "superseded_by": null, "human_review": false
}
```

## 6. UI guidance that matters for safety (from the event rules)

- Label the AI output as a **recommendation**. Approve/Deny buttons belong to the coordinator.
- `unknown` reads as "Not in packet / can't tell", never as "OK".
- `[REDACTED]` in page text means removed for privacy, not missing.
- Show `verified: false` evidence and `conflict` reasons prominently. They are where the AI is most likely wrong.
- Drafts and the Kipu payload are not sent anywhere. Say "Draft" / "Mock" in the UI.

## 7. Behaviour notes for the UI (v1.1)

- Fields typed `x?: T` are **present with `null`** when unknown; they are not omitted. Treat `null` and missing the same.
- **Disable Approve/Deny/Request info while a decision request is in flight.** Double-clicks are only partly guarded on the server.
- `POST /api/referrals/{id}/decision` may return `500` with `detail: "Approval failed and was rolled back: ..."`. The bed is held again, so the coordinator can retry.
- `GET /api/referrals/{id}/pages/{page}` and `GET /api/referrals/{id}/bed-matches` return `409` until analysis finishes.
- `GET /api/referrals/{id}/pages/{page}/image` returns `404` for an out-of-range page or a missing upload.
- `POST /api/beds` returns `409` if the bed id already exists.
- `PATCH /api/beds/{id}` returns `409` when changing the status of a bed with an active assignment. Discharge it first.
- `POST /api/beds/import` never fails the whole file for a bad row. Each problem row appears in `errors[]` as `"row N: ..."`. Show them.
- A passing finding whose quote couldn't be matched to the packet shows up as a `review_reasons[]` item of kind `unverified_evidence` and makes the decision `needs_review`. That's intentional (guardrail).

## 9. Knowledge core (v3)

```ts
type LessonKind = "bed_preference" | "rule_interpretation" | "decision_precedent" | "county_mapping";
type LessonStatus = "proposed" | "approved" | "rejected" | "retired";

interface CaseProfile {                  // coarse, de-identified: no quotes, no names
  county?: string; payer?: string; sex: Sex; wheelchair?: boolean; oxygen?: boolean; isolation?: boolean;
  mobility_aid?: string; systems?: string[]; flagged_rule_ids?: string[];  // absent = not evaluated; [] = evaluated, none
}
interface Precedent {
  lesson_id: number; similarity: number;   // 0..1, shown only at >= 0.5
  summary: string; ai_decision: string; coordinator_decision: string; note: string;
}
interface LessonView {
  id: number; kind: LessonKind; status: LessonStatus; summary: string;
  created_at: string; created_by: string; source_referral_id?: string;
  profile: CaseProfile; payload: Record<string, any>;
  note: string; needs_note: boolean;       // needs_note: coordinator left no note; reviewer may add one when approving
  reviewed_by?: string; reviewed_at?: string; times_applied: number;
}
interface LessonReview { reviewer: string; note?: string; }  // a non-empty note replaces the note and clears needs_note
```

- Lessons are proposed automatically when a coordinator decision differs from the AI (decision, rule override status, bed choice) or the coordinator changes the AI's county. They influence nothing until staff **approve** them. Rejected, proposed and retired lessons are never used.
- Build a staff queue from `GET /api/knowledge?status=proposed`. Approve/reject only from `proposed`, retire only from `approved`; anything else is `409`.
- On the review card show `recommendation.precedents` ("similar past cases") and the `knowledge` tree step. Held and alternative beds may carry the match note "Preferred for similar cases (lesson #N)"; a county basis may end with "(learned from lesson #N)". `ReferralDetails.knowledge_used` lists the lesson ids.
- Lessons never bypass hard rules: beds are only re-ranked among beds that already passed the county/program/availability/sex/ADA filters, and rule lessons only add guidance to the AI's evaluation. The deterministic decision tree and the coordinator's final decision are unchanged.

## 8. County-aware matching and the bed board (v2)

- Each **facility** (one census sheet) has a `county` and a `program_type`. Referrals only match **active, `recuperative_care` facilities in the patient's county**. Facilities with unknown county never match. POC data uses placeholder counties/programs (facility `notes` say so). Let staff edit them via `PATCH /api/facilities/{id}`.
- **Patient county**: the AI extracts the health plan (with a cited quote); code maps single-county plans (CalOptima → Orange, L.A. Care → Los Angeles). Show `ReferralView.county.basis` and a county picker. If `county` is null, the review card shows `county_needed` and no bed is held. `POST /api/referrals/{id}/county` confirms/overrides; the response has the newly held bed.
- **Bed board**: group `GET /api/beds` by `site`, color by `status` (`needs_cleaning` is new), show `stay` summary. Clicking a bed → `GET /api/beds/{id}` for the **patient info** section (`current_stay.extra`).
- **Census import**: `POST /api/census/import` with IHH's workbook; show the per-sheet report (skipped rows and conflicts). Re-importing is safe.

## Changelog
- v3 (2026-10-17): knowledge core (additive): `/api/knowledge` list/export/approve/reject/retire, `LessonView`, `LessonReview`, `CaseProfile`, `Precedent`, `Recommendation.precedents`, `TreeStep.node = "knowledge"`, `ReferralDetails.knowledge_used`. See §9.
- v1 (2026-10-17): initial contract.
- v2 (2026-10-17): census-backed BMS + county matching (additive): facilities endpoints, bed detail with patient info, census import, county confirm, `needs_cleaning` bed status, `county_needed` review reason, `ReferralView.county`, `BedView.site_id/county/program_type/stay`, `BedSummary.needs_cleaning`. See §8.
- v2.1 (2026-10-17): `BedSummary.needs_cleaning`, `BedUpdate.status` accepts `needs_cleaning`, `AssignmentView.referral_id` optional (null for census/seed stays), county route `422`, census route `413`, `FacilityUpdate` null-clears county. Census re-import reports a conflict and leaves the bed unchanged when it has an active hold or a referral stay.
- v1.1 (2026-10-17): behaviour notes (§7) from code review: nulls, button disabling, 409/404/500 cases, per-row import errors, unverified-evidence reviews. No field or route changes.
