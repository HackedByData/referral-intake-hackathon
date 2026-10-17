<img src="web/raft-logo.png" alt="Raft logo" width="180">

# Raft

Referral intake and bed management for Illumination Health + Home (IHH) recuperative care.

> For IHH referral coordinators: upload a faxed referral packet, get a cited recommendation and a held bed in minutes, and decide with one click.

**Team:** RAFT
**Members:** Devin McCaw, Fernanda Tamayo, Aditi Bhavsar
**Challenge:** 1 Referral intake
**Built at:** Orange County | Claude Impact Lab, October 17, 2026, with Illumination Health + Home

## The problem

An Illumination Health + Home (IHH) referral coordinator reads each faxed hospital referral (15 to 35 pages, often sideways, upside down, out of order or duplicated) against about six pages of admission criteria. Hospitals send the same referral to several providers and IHH promises an answer within 2 hours, so a slow answer can mean a patient who is too sick to recover outside is discharged to the street. The deciding fact is often deep in the chart and may contradict the one-page referral form, and "not in the packet" has to be treated as unknown, not as normal.

## What we built

A FastAPI backend with two parts.

1. **Referral intake.** The coordinator uploads a packet PDF. The tool transcribes every page, checks the packet against 155 IHH admission rules, and returns a recommendation (approve, deny or needs_review) with a cited quote and page number for each finding. It also lists open questions for the hospital, matches and holds a bed, and drafts a reply to the hospital. The coordinator decides. Approval assigns the bed and writes a MOCK Kipu EHR payload to disk.
2. **Bed management.** A small bed system (beds, assignments, discharge, CSV/XLSX import) that replaces the Excel sheet and feeds bed matching. It is now census-backed and county-aware:
   - **POC seed.** The default seed is 18 placebo facilities with invented "Test Client" occupants. The counties and programs are placeholders to confirm with IHH.
   - **Census import.** `POST /api/census/import` takes IHH's census workbook (one sheet per facility) and reports what it created, skipped and could not reconcile.
   - **County-aware matching.** The AI extracts the health plan with a quote, and `criteria/plan_county.json` maps single-county plans to a county. The coordinator confirms it via `POST /api/referrals/{id}/county`. Only active recuperative-care facilities in that county match.
   - **Bed detail.** Each bed shows a patient-info section for its current occupant, plus stay history.

3. **Knowledge core.** When a coordinator's decision differs from the AI's (decision, rule override, bed choice, county), the system proposes a de-identified **lesson**. Staff approve it through `/api/knowledge`.
   - **Used only after approval.** Approved lessons add guidance to the matching rule's prompt, re-rank beds that already passed the hard filters, extend the plan-to-county map, and show similar past cases to the coordinator.
   - **Transparent.** Every use is listed in `knowledge_used`, a "knowledge" decision-tree step, bed notes and the county basis. The eval ignores lessons unless run with `--with-knowledge`. See `docs/modules/knowledge.md`.

The frontend is being built separately in Claude Design against `docs/api-contract.md`.

**Demo video:** [link]

## How Claude is used

- **Built with Claude.** Claude Code wrote the backend from a design spec and an implementation plan in this repo (`docs/superpowers/`).
- **Inside the tool, model `claude-opus-5-5` via the Anthropic SDK** (all calls go through `app/llm.py`):
  - Per-page vision transcription (effort low): each PDF page is rendered and transcribed to markdown, with orientation and legibility noted.
  - One case-summary call plus 8 parallel rule-group calls (effort high). The groups share a packet prefix that is marked for prompt caching. Each rule gets a status, a confidence and a verbatim quote with its page.
  - Structured outputs for every call, and explicit refusal handling.
- **Deterministic code, not Claude, makes the call.** `app/rules/verify.py` checks that every quote really appears on the cited page. `app/rules/tree.py` walks the decision tree: exclusions, must-haves, general eligibility, human review, missing information, unverified evidence, conflicts, then bed. A deny needs verified evidence, and one rule (OTH-2) never denies on its own.
- **What a person checks.**
  - Every quote is shown with its page and a link to the page image.
  - Quotes that could not be verified are flagged.
  - The coordinator makes every decision; the tool only recommends.
  - Overrides of the recommendation are written to an audit log.

## Try it

```bash
git clone <this repo> && cd referral-intake-hackathon
python -m venv .venv                              # macOS/Linux: python3 -m venv .venv
.venv/Scripts/pip install -r requirements.txt     # macOS/Linux: .venv/bin/pip
cp .env.example .env                              # then set ANTHROPIC_API_KEY in .env
.venv/Scripts/uvicorn app.main:app --port 8000    # macOS/Linux: .venv/bin/uvicorn
# open http://localhost:8000/docs
# upload a packet PDF with POST /api/referrals, then poll GET /api/referrals/{id}
```

Tests (no API key needed): `.venv/Scripts/python -m pytest -q` (macOS/Linux: `.venv/bin/python -m pytest -q`).

Eval against IHH's decisions for the 11 real packets (needs `ANTHROPIC_API_KEY`):

```bash
.venv/Scripts/python -m eval.run --packets-dir ../claude-impact-lab-oc/challenges/1-referral-intake/packets --decisions ../claude-impact-lab-oc/challenges/1-referral-intake/eval/decisions.json
```

Needs: Python 3.10+ (tested on 3.14) with `venv` and pip, and an Anthropic API key. Settings live in `.env` (see `.env.example`); no keys are in the repo. The packets must be downloaded separately per the event repo and are never committed here. Beds are seeded on first start according to `SEED_MODE` (`poc` default | `demo` | `none`); generated data goes to `data/` (gitignored). If you ran an older version, the server backs up an incompatible data/app.db automatically.

### Hosted demo (Vercel)

The repo deploys to Vercel as-is (`app/main.py` is auto-detected; `vercel.json` sets the function duration). The hosted
copy is a **demo**: open `/?demo=1` for the referral flow with invented data, and use the Beds tab, census import and
county/knowledge APIs live. On Vercel the database lives in `/tmp` per function instance, so data resets and may differ
between requests, and request bodies are capped at 4.5 MB (most packets are larger). Run real packets and the eval
locally. Real packets are never uploaded to the hosted demo.

## What is real and what is mocked

| Part | Status |
|---|---|
| PDF page transcription (vision) | working |
| Rule evaluation with page-cited quotes | working |
| Quote verification | working |
| Decision tree (approve / deny / needs_review) | working |
| Bed management and bed matching | working; the demo beds are invented, real sheets can be imported via CSV/XLSX |
| Census import | working |
| Facility counties/programs | placeholders |
| Bed occupants in seed | invented placebo |
| Coordinator decision, override audit log | working |
| Kipu EHR upload | mocked; writes JSON to `data/outbox/kipu/` and connects to nothing |
| Hospital messages | drafts only; nothing is ever sent |
| Frontend | in progress; a teammate's Claude Design UI built against `docs/api-contract.md` |

## Where it gets things wrong

Dates in the packets are blacked out, so rules that depend on a time window (for example insulin in the last 24 hours, alcohol use in the last 72 hours, COVID swab age) usually come back unknown, and the tool says "can't tell from this packet" rather than guessing. Quote verification is fuzzy (normalized match, then a similarity threshold, with numbers and negations required to appear on the page), so a near-miss quote can slip through or a correct one can be flagged. Handwriting, very faint fax text and badly rotated pages are hard to transcribe, and a misread there flows into the rules. The admission rules are partly judgment calls and some wording is unclear, so the tool surfaces those cases to the coordinator instead of choosing silently. We would not trust it to approve or deny without a person reading the cited evidence.

**Eval (11 real packets vs IHH's actual decisions, 2026-10-17, prompt 2026-10-17.2; full table in [docs/eval-scorecard.md](docs/eval-scorecard.md)):**

| Result | Count |
|---|---|
| Matches IHH | 3 / 11 (all three denials: R01, R09, R10) |
| **False approvals** (we approve, IHH denied) | **0** |
| False denials | 3 (R03, R07, R08) |
| Needs review (coordinator decides) | 5 (4 IHH approved, 1 IHH denied) |

What the misses tell us:
- **No packet came back "approve".** Every packet has 18–29 "missing information" items (e.g. the 30-day medication supply, and time-window rules that dates-redacted packets cannot answer). The tree sends unknown required items to review instead of guessing, which is why there are no false approvals, and also no automatic approvals. The coordinator still decides; the tool hands them the questions to ask the hospital.
- **R03 (CARD-5) and R08 (MOB-1/MOB-3):** the written criteria say ineligible (ejection fraction below 25% after a heart attack; walking with a walker under supervision), with verified quotes, but IHH approved. That is IHH's practice going beyond its written rules: exactly what the knowledge core is for (a coordinator override becomes a staff-approved lesson).
- **R07 (RSP-X5, "requires BiPAP"):** a real model error. BiPAP was ordered, but the packet does not say it is needed after discharge; the model's own reasoning says so, yet it marked the rule failed instead of unknown. Next prompt fix: an exclusion fails only when the condition is established for after discharge.

## Next steps

- To pilot: run the eval on more packets, tune the prompts with the coordinators, replace the mocked Kipu writer with a real integration approved by IHH, and add sign-in for coordinators.
- Real bed data: import IHH's actual sheet and keep it current.
- A "re-refer when ready" outcome, since today the answer is only approve or deny.
- With one more week: better handling of rotated and duplicate pages, a side-by-side page viewer, and a feedback button so coordinator corrections improve the prompts.

## Data and privacy

This project uses only data from the event repo and public sources. It contains no real
patient or client information. It does not connect to any Illumination Health + Home system. The census workbook used for testing is synthetic and kept outside the repo, and the committed seed contains only invented occupants.

## License

[MIT](LICENSE)
