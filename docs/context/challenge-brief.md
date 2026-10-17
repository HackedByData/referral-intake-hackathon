# Challenge brief: Referral intake (Claude Impact Lab OC, 2026-10-17)

Source: https://github.com/travcjohnson/claude-impact-lab-oc/tree/main/challenges/1-referral-intake
This file condenses that challenge for agents working in this repo. When in doubt, the upstream repo wins.

## The problem

> How might we help a coordinator say yes in minutes, so a patient doesn't leave the hospital for the street?

- **User:** the IHH (Illumination Health + Home) referral coordinator. Beneficiary: a homeless patient ready
  to leave the hospital but too sick to recover outside.
- **Today:** each referral is 15–35 faxed pages, read by hand against ~6 pages of criteria. Hospitals send the
  same referral to several providers, so a slow answer can mean discharge to the street.
- **Service promise:** IHH answers hospitals within **2 hours**. If denied, a brief explanation is given.
- **Outcome today is binary:** approve or deny. No written "re-refer when ready" state exists (a known gap).

## Why it's hard

- The referral form is one person's summary; the deciding fact is often deep in the chart and may contradict the form.
- "Not in the packet" means **unknown**, not normal.
- Two rule documents disagree. The **internal admission criteria (outline) win** over the referral form (RC0725).
  Where the outline is silent (tuberculosis, refusing detox), the form's exclusion applies.
- Packets are image-only faxes (no text layer), 200 dpi, pages sideways/upside down/out of order/duplicated,
  some from earlier stays. R10 has no IHH referral form at all.
- **Dates are blacked out.** Rules depending on hours (insulin <24h, alcohol 72h, COVID swab 24h) usually can't be
  checked → tool must say "can't tell from this packet".
- **Black boxes = redacted for privacy**, never "missing document".

## Data available (upstream repo, NOT this repo)

| Item | Location upstream | Notes |
|---|---|---|
| 11 packets R01–R11 (282 pages) | Google Drive; locally in the downloaded ZIP | **Never commit.** Point code at a path outside git. |
| Admission criteria (adults) | `criteria/admission-criteria.md` / `.json` | 48 rules with IDs + `by_system` eligible/ineligible lists + `referral_checklist` (DOC-1..10) + `referral_form_screen` (FORM-M*, FORM-X*) |
| Children's criteria | `criteria/childrens-admission-criteria.md` | Optional; no child packets exist |
| Referral form + checklist | `criteria/referral-form-and-checklist.md` | Hospital-facing, stricter, loses to the outline |
| Ground truth | `eval/decisions.json` | R01 deny, R02–R04 approve, R05 deny, R06–R08 approve, R09 deny, R10 deny, R11 approve. **Keep out of the reviewer's sight**; use only for scoring. No reasons recorded. |

### Rule kinds in `admission-criteria.json`

`must` (has to meet), `exclude` (ineligible if true), `document` (hospital must send), `conditional` (allowed only if
an arrangement is confirmed before admission), `ask` (question, not pass/fail), `note` (context; OTH-2 sex offender
has `human_review: true`). Counts: must 15, document 11, exclude 8, ask 8, conditional 5, note 1.

### Settled conflicts / known gaps (show these to the coordinator, never pick silently)

- Sex offender: case-by-case (OTH-2), violent offenses likely denied. **Never auto-deny; flag for human.**
- Wheelchair: MOB-3/MOB-4 supersede FORM-M6/FORM-X3. Wheelchair-dependent + independent transfer = eligible.
- TB, refusing detox: form exclusion applies.
- Unclear: "O2 baseline ≥94% with physician explanation", A1C sentence cut off, "FSP" undefined, insulin 24h clock,
  resolved DKA, C. diff end condition, troponin after MI, COVID swab age at arrival, "complicated large abscess",
  hard-to-reach wound list, home-health threshold wording varies.

## Non-negotiable event rules

1. **No identifiable patient info.** Never copy packets into this repo; never try to identify anyone. If an unredacted
   identifier is spotted, stop and tell an organizer.
2. **Prototype only.** Nothing connects to/reads/writes IHH systems (**this includes Kipu**), nothing sends real
   email/text/fax. Draft, don't send.
3. **Build in quality control.** AI makes mistakes; the design must catch them.
4. **Never invent facts.** No made-up page numbers, quotes, lab values. Unknown is "unknown".
5. No medical/legal/immigration advice in client-facing output.
6. No secrets in git (`.env` ignored). Repo must be public, MIT licensed.
7. Demo honestly: say what is mocked and where the tool is wrong.

## Judging (weights)

- 40% Day-one impact (ready to use, helps IHH day one)
- 35% Product and idea (easy to use, creative, useful)
- 25% Demo clarity
- **Judges may hand us an unseen packet and ask us to run it live.**
- Submission due **4:00 PM**; README must follow upstream `templates/TEAM-README.md`.
