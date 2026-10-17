# Workflow + referral API (`app/workflow/`, `app/api/referrals.py`)

**Lifecycle:** `uploaded → transcribing → analyzing → ready_for_review → approved | denied | info_requested`, or
`failed` (error text shown; `POST /retry` re-runs and reuses the OCR cache). On startup, referrals left in flight are
marked failed ("interrupted").

**Pipeline** (`pipeline.py`, FastAPI BackgroundTask): transcribe → evaluate → verify → decide → hold the top bed
(4 h TTL). The full `AnalysisResult` is stored as JSON on the referral row.

**Decisions** (`decisions.py`): approve assigns the held bed (or `bed_id`), then compensates on failure (discharges the bed, re-holds it, returns 500, referral stays reviewable), and writes the mock
Kipu payload to `data/outbox/kipu/{id}.json`; deny releases the hold and drafts a denial; request_info keeps the hold
and drafts the hospital questions. Every decision is audited with `ai_recommendation` and `agreed_with_ai`.
Double submits → 409.

**Drafts are never sent.** Kipu is mocked (event rule: no IHH systems).

**Testing:** `pytest tests/test_workflow_parts.py tests/test_pipeline.py tests/test_decisions.py tests/test_api_referrals.py`.

**County:** the pipeline sets `suggested_county` and `bed_needs.county` before matching; no county means no hold. `POST /api/referrals/{id}/county` (status `ready_for_review` or `info_requested`) records the coordinator's county, releases the old hold, re-matches without AI and re-holds; audit action `county_confirmed`.
