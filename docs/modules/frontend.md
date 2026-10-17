# Coordinator UI (`web/`)

**Purpose:** the screens a coordinator uses. Plain HTML/CSS/JS, no build step. FastAPI serves it at `/`
(`app/main.py` mounts `web/` after the API routers), so run the API and open `http://localhost:8000/`.

**Flow (from the teammate's design brief):**
1. **Upload packet**: send the PDF (`POST /api/referrals`), show progress while polling, retry on failure, list recent referrals.
2. **Check results**: the AI recommendation banner ("you make the final decision") plus three tables, each with
   **Confirm** and **Edit**:
   - *Bed*: held bed + other matches (`/bed-matches`), county used for matching. Edit = choose another bed or change
     the county (`POST /county`, which re-holds a bed).
   - *Why*: review reasons, contradictions, unverified quotes, questions for the hospital, then the **admission
     criteria checklist**: every IHH criterion from `GET /api/criteria` in the outline's order (general criteria,
     requirements, exclusions, arrangements, documents, accepted conditions by body system, questions, referral form
     screen). Each line shows IHH's wording, a result phrased for its kind ("Met", "Not present", "In the packet",
     "Can't tell from the packet"), and the AI's reason with page-linked quotes. Groups needing attention open first;
     superseded form rules say which outline rule replaces them; a criterion with no AI result shows "Not checked".
     Edit = override a criterion's result; a reason is required (`rule_overrides`).
   - *Patient*: `summary` fields, `null` shown as "Unknown". Edit = corrections, sent as decision `notes`
     (the API has no patient-edit route, so the AI analysis itself is not changed).
3. **Decide**: Approve (all three tables confirmed + a bed), Request info or Deny (Why + Patient confirmed). Then the
   confirmation, drafts with Copy buttons, and the mock Kipu reference. Everything says Draft / Mock.

**Beds tab:** all sites (cards, recuperative care sites that take referrals first) -> one site's rooms and beds ->
one bed (patient info from `current_stay`, `extra` shown generically, discharge, status, features). Statuses and bed
features are config objects at the top of `app.js` (`BED_STATUS`, `BED_FEATURES`); add a row there when IHH's sheet
gains a field. Census beds store "101-A" as both room and label, so the UI derives the room from the label.

**Demo mode:** `/?demo=1` fakes only `/api/referrals*` with invented data (`web/mock.js`); beds come from the real
server. Use it to review the flow without an API key or a packet.

**Other:** the URL hash keeps your place on refresh (`#R-7F3A2C/review`). `?api=http://host:8000` points the page at
another server. Your name is remembered in the browser.
