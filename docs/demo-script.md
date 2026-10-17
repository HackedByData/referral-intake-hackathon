# Demo script (2 minutes)

Have ready: the server running, one packet uploaded in advance (a `needs_review` case), and one packet to upload live. Fill in the bracketed numbers from the real run before recording.

## 0:00 to 0:15 - The problem

"A hospital faxes IHH a referral of [page count] pages. The coordinator reads it against the admission criteria, and IHH has promised an answer in 2 hours. If it is slow, the patient may be discharged to the street."

## 0:15 to 0:35 - Upload and progress

Upload the packet PDF. Show the progress states as it transcribes each page and checks the rules. Say: "Claude reads every page and checks 155 rules in parallel."

## 0:35 to 1:00 - Summary card

Show the card: the decision (approve, deny or needs_review), the held bed, and the three main reasons. Say: "This is a recommendation. The coordinator decides."

## 1:00 to 1:20 - Evidence

Click details. Open one evidence quote and show its page number, then open the page image. Say: "Every quote is checked by code against the page. If it can't be found, it is flagged as unverified."

## 1:20 to 1:35 - A needs_review case

Open the pre-loaded needs_review referral. Show the questions for the hospital and a rule marked unknown because the dates are blacked out. Say: "Not in the packet means unknown, not normal. The tool says what it can't tell."

## 1:35 to 1:50 - Approve

Approve the first referral. Show `/api/beds/summary`: the bed is now occupied. Show the Kipu payload in drafts. Say: "Kipu is mocked, it writes a JSON file. Hospital messages are drafts and are never sent."

## 1:50 to 2:00 - Scorecard

Show `docs/eval-scorecard.md`: [X]/11 match IHH's decisions, [0] false approvals, and the misses stated honestly. Close with: "It speeds up the coordinator. It doesn't replace them."

If the live scorecard has not been run yet, say so: "The eval run is pending."
