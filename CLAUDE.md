# CLAUDE.md

Hackathon prototype (Claude Impact Lab OC, 2026-10-17) for Illumination Health + Home (IHH): AI-assisted referral
intake + bed management. Submissions due 4:00 PM.

Read first: `docs/context/challenge-brief.md`, then `docs/superpowers/specs/2026-10-17-referral-intake-design.md`,
then `docs/superpowers/plans/2026-10-17-referral-intake.md`. Module notes are in `docs/modules/`.

## Rules that do not bend
- Never copy packet PDFs, the ZIP, OCR output or quotes from packets into this repo or its history. Packets live in
  `../claude-impact-lab-oc/challenges/1-referral-intake/packets/`. Generated data lives in `data/` (gitignored).
- If a packet page shows an unredacted identifier: stop and tell the user to find an organizer.
- Nothing connects to IHH systems (Kipu is mocked) or sends real messages. Drafts only.
- Never invent facts. Unknown is "unknown". `[REDACTED]` is not "missing".
- `app/` never reads `eval/decisions.json`.
- Secrets only in `.env` (gitignored).

## Working here
- Python venv: `.venv`. Run tests: `.venv/Scripts/python -m pytest -q`. Run API: `.venv/Scripts/uvicorn app.main:app --reload`.
- Coordinator UI: `web/` (plain JS, served at `http://localhost:8000/`; `/?demo=1` for invented sample referrals). See `docs/modules/frontend.md`.
- Shared types: `app/schemas.py`. Rule IDs come from `criteria/admission-criteria.json`.
- Model: `claude-opus-5-5` via `app/llm.py` only (adaptive thinking, explicit effort, refusal handling).
- When criteria are unclear or sources disagree, surface it to the coordinator. Don't pick silently.
- Commit often; never commit `data/`, PDFs, or `.env`.
