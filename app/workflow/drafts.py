"""Deterministic message drafts for the coordinator to edit and send themselves. Never sent by this app."""
from app.schemas import Recommendation, Rule, RuleResult

GEN_IDS = ("GEN-1", "GEN-2", "GEN-3")
HEADER ="DRAFT: not sent. Review and edit before sending.\n\n"


def denial_draft(referral_id: str, recommendation: Recommendation, results: list[RuleResult],
                 rules_by_id: dict[str, Rule], coordinator: str, notes: str,
                 overridden_fail: frozenset[str] = frozenset()) -> str:
    """`results` must already be the effective results (coordinator overrides applied); `overridden_fail` names
    rules the coordinator set to fail, which may be cited without verified evidence."""
    failed = [r for r in results if r.status == "fail" and r.rule_id in rules_by_id
              and rules_by_id[r.rule_id].kind in ("must", "exclude") and r.rule_id not in GEN_IDS
              and (any(e.verified for e in r.evidence) or r.rule_id in overridden_fail)]
    lines = [f"- {r.reasoning.strip()} (IHH admission criteria {r.rule_id})" for r in failed] or \
            ["- See coordinator notes."]
    body = (f"Re: Recuperative care referral {referral_id}\n\n"
            "Thank you for referring this patient to Illumination Health + Home. After reviewing the referral "
            "packet against our recuperative care admission criteria, we are not able to accept this referral "
            "at this time, for the following reason(s):\n\n" + "\n".join(lines) + "\n")
    if notes:
        body += f"\nAdditional notes: {notes}\n"
    body += ("\nIf the patient's situation changes, please feel free to send a new referral.\n\n"
             f"{coordinator}\nReferral Coordinator, Illumination Health + Home")
    return HEADER + body


def info_request_draft(referral_id: str, recommendation: Recommendation, coordinator: str, notes: str) -> str:
    questions = [f"{i}. {q.question} ({q.rule_id})" for i, q in enumerate(recommendation.hospital_questions, 1)] or \
                ["1. See notes below."]
    body = (f"Re: Recuperative care referral {referral_id}: information needed\n\n"
            "Thank you for the referral. To finish our review we need the following:\n\n"
            + "\n".join(questions) + "\n")
    if notes:
        body += f"\nNotes: {notes}\n"
    body += f"\nThank you,\n{coordinator}\nReferral Coordinator, Illumination Health + Home"
    return HEADER + body
