# API contract (frontend ↔ backend)

**Status:** v1, frozen for the hackathon (2026-10-17). Backend field names match `app/schemas.py` exactly.
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
| GET | `/api/assignments` | `?active=true` (default) | `AssignmentView[]` |
| POST | `/api/assignments/{id}/discharge` | | `AssignmentView` · `404` |
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
type BedStatus = "available" | "held" | "occupied" | "out_of_service";
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
}

interface Recommendation {
  decision: Decision;
  headline: string;
  top_reasons: string[];                 // ≤3
  review_reasons: ReviewReason[];
  hospital_questions: HospitalQuestion[];
  tree: TreeStep[];                      // decision path, in evaluation order
  held_bed?: BedOption;
  alternative_beds: BedOption[];
}

interface ReviewReason {
  kind: "missing_info" | "judgment_call" | "unverified_evidence" | "conflict" | "no_bed";
  rule_ids: string[];
  detail: string;
}

interface HospitalQuestion { rule_id: string; question: string; }

interface TreeStep {
  node: "exclusions" | "must_rules" | "general_eligibility" | "human_review"
      | "missing_info" | "unverified" | "conflicts" | "bed";
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
}

interface BedCreate {
  site: string; room: string; label: string; id?: string;
  sex_designation?: SexDesignation; wheelchair_accessible?: boolean; ground_floor?: boolean;
  oxygen_ok?: boolean; isolation_ok?: boolean; notes?: string;
}

interface BedUpdate {                    // all optional; only sent fields change
  sex_designation?: SexDesignation; wheelchair_accessible?: boolean; ground_floor?: boolean;
  oxygen_ok?: boolean; isolation_ok?: boolean; status?: "available" | "out_of_service"; notes?: string;
}

interface BedSummary {
  total: number; available: number; held: number; occupied: number; out_of_service: number;
  by_site: { site: string; total: number; available: number }[];
}

interface AssignmentView {
  id: number; bed_id: string; referral_id: string; client_label: string;
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
      {"node": "unverified", "outcome": "clear", "rule_ids": [], "explanation": "All failing findings are backed by verified quotes."},
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

## Changelog
- v1 (2026-10-17): initial contract.
- v1.1 (2026-10-17): behaviour notes (§7) from code review: nulls, button disabling, 409/404/500 cases, per-row import errors, unverified-evidence reviews. No field or route changes.
