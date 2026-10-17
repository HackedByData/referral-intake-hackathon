"""Deterministic decision tree over verified rule results. Spec section 7.5. Pure function, no I/O."""
from app.schemas import (
    BedOption, CaseSummary, HospitalQuestion, Recommendation, ReviewReason, Rule, RuleResult, TreeStep,
)

GEN_IDS = ("GEN-1", "GEN-2", "GEN-3")
APPROVE_HIGHLIGHTS = ("GEN-1", "GEN-2", "GEN-3", "MOB-3", "MOB-4", "ADL-1", "COG-1", "COG-2")


def _verified(r: RuleResult) -> bool:
    return any(e.verified for e in r.evidence)


def _cite(r: RuleResult) -> str:
    pages = sorted({e.page for e in r.evidence if e.verified})
    return f" (p.{', '.join(map(str, pages))})" if pages else ""


def _first_sentence(text: str) -> str:
    return text.split(". ")[0].rstrip(".")


def _reason_line(r: RuleResult) -> str:
    return f"{_first_sentence(r.reasoning)}{_cite(r)} [{r.rule_id}]"


def _step(node: str, hits: list[RuleResult] | list[str], clear: str, triggered: str) -> TreeStep:
    ids = [h if isinstance(h, str) else h.rule_id for h in hits]
    return TreeStep(node=node, outcome="triggered" if ids else "clear", rule_ids=ids,
                    explanation=triggered if ids else clear)


def decide(rules: list[Rule], results: list[RuleResult], summary: CaseSummary,
           bed_options: list[BedOption]) -> Recommendation:
    meta = {r.id: r for r in rules}
    res = {r.rule_id: r for r in results if r.rule_id in meta}
    # Rules with no result at all are treated as not evaluated (unknown).
    for rule in rules:
        if rule.id not in res and rule.superseded_by is None:
            res[rule.id] = RuleResult(rule_id=rule.id, status="unknown", confidence="low",
                                      reasoning="Not evaluated; check manually.")
    kind = {rid: meta[rid].kind for rid in res}
    human = {rid for rid in res if meta[rid].human_review}

    steps: list[TreeStep] = []
    reasons: list[ReviewReason] = []
    questions: list[HospitalQuestion] = []

    # 1. exclusions
    excl = [r for r in res.values() if kind[r.rule_id] == "exclude" and r.rule_id not in human
            and r.status == "fail" and _verified(r)]
    steps.append(_step("exclusions", excl, "No exclusion found with verified evidence.",
                       "Exclusion present with verified evidence."))

    # 2. must rules (GEN-* handled separately)
    must_fail = [r for r in res.values() if kind[r.rule_id] == "must" and r.rule_id not in GEN_IDS
                 and r.rule_id not in human and r.status == "fail" and _verified(r)]
    steps.append(_step("must_rules", must_fail, "No required criterion clearly unmet.",
                       "Required criterion not met, with verified evidence."))

    # 3. general eligibility: at least one of GEN-1..3
    gen = [res[g] for g in GEN_IDS if g in res]
    gen_pass = any(g.status == "pass" for g in gen)
    gen_all_fail = bool(gen) and all(g.status == "fail" and _verified(g) for g in gen)
    gen_unknown = [g for g in gen if g.status == "unknown"]
    steps.append(TreeStep(
        node="general_eligibility", outcome="triggered" if (gen and not gen_pass and gen_all_fail) else "clear",
        rule_ids=[g.rule_id for g in gen] if not gen_pass else [],
        explanation="Meets at least one general admission criterion." if gen_pass else
        ("Meets none of the general admission criteria." if gen_all_fail else
         "General admission criteria not established from the packet.")))
    if gen and not gen_pass and not gen_all_fail:
        reasons.append(ReviewReason(kind="missing_info", rule_ids=[g.rule_id for g in gen_unknown] or list(GEN_IDS),
                                    detail="Housing / support situation that qualifies the patient is not established."))

    # 4. human review
    human_hits = [r for r in res.values() if r.status == "needs_human"
                  or (r.rule_id in human and r.status not in ("pass", "not_applicable"))]
    steps.append(_step("human_review", human_hits, "No case-by-case judgment needed.",
                       "IHH rules leave this to staff judgment."))
    for r in human_hits:
        reasons.append(ReviewReason(kind="judgment_call", rule_ids=[r.rule_id], detail=_first_sentence(r.reasoning)))

    # 5. missing information
    missing = [r for r in res.values() if r not in human_hits and r.rule_id not in GEN_IDS and (
        (kind[r.rule_id] in ("must", "exclude") and r.status == "unknown")
        or (kind[r.rule_id] == "conditional" and r.status in ("unknown", "fail"))
        or (kind[r.rule_id] == "document" and r.status in ("unknown", "fail")))]
    steps.append(_step("missing_info", missing, "Nothing required is missing.",
                       "Information needed to decide is missing from the packet."))
    for r in missing:
        reasons.append(ReviewReason(kind="missing_info", rule_ids=[r.rule_id], detail=_first_sentence(r.reasoning)))
        q = r.hospital_question or f"Please send or confirm: {meta[r.rule_id].text[:160]}"
        questions.append(HospitalQuestion(rule_id=r.rule_id, question=q))

    # 6. unverified failures can never deny
    unverified = [r for r in res.values() if kind[r.rule_id] in ("must", "exclude") and r.rule_id not in human
                  and r.rule_id not in GEN_IDS and r.status == "fail" and not _verified(r)]
    steps.append(_step("unverified", unverified, "All failing findings are backed by verified quotes.",
                       "A failing finding could not be matched to the packet text; check it by hand."))
    for r in unverified:
        reasons.append(ReviewReason(kind="unverified_evidence", rule_ids=[r.rule_id],
                                    detail=f"{_first_sentence(r.reasoning)} (quote not found on the cited page)"))

    # 7. conflicts touching anything flagged
    flagged = {r.rule_id for r in excl + must_fail + human_hits + missing + unverified}
    conflicts = [c for c in summary.conflicts if set(c.related_rule_ids) & flagged]
    steps.append(_step("conflicts", [i for c in conflicts for i in c.related_rule_ids],
                       "No contradictions affecting the decision.", "Pages of the packet disagree on this."))
    for c in conflicts:
        reasons.append(ReviewReason(kind="conflict", rule_ids=c.related_rule_ids, detail=f"{c.topic}: {c.why_it_matters}"))

    deny = bool(excl or must_fail or (gen and not gen_pass and gen_all_fail))
    decision = "deny" if deny else ("needs_review" if reasons else "approve")

    # Bed node
    held = None
    alternatives: list[BedOption] = []
    if decision != "deny":
        if bed_options:
            held, alternatives = bed_options[0], bed_options[1:]
        else:
            reasons.append(ReviewReason(kind="no_bed", rule_ids=[], detail="No available bed matches this patient's needs."))
    steps.append(TreeStep(node="bed", outcome="clear" if (held or decision == "deny") else "triggered",
                          rule_ids=[], explanation=f"Held bed {held.bed_id}." if held else
                          ("Not applicable (deny)." if decision == "deny" else "No matching bed available.")))

    if decision == "deny":
        hits = excl + must_fail or gen
        top = [_reason_line(r) for r in hits][:3]
        headline = f"Recommend deny: {len(excl) + len(must_fail) or 1} criterion not met"
    elif decision == "needs_review":
        top = [f"{rr.detail} [{', '.join(rr.rule_ids)}]" if rr.rule_ids else rr.detail for rr in reasons][:3]
        headline = f"Needs review: {len(reasons)} item(s) to resolve before deciding"
    else:
        top = [_reason_line(res[i]) for i in APPROVE_HIGHLIGHTS if i in res and res[i].status == "pass"][:3]
        headline = (f"Recommend approve: meets admission criteria; bed {held.label} in room {held.room} held"
                    if held else "Recommend approve: eligible, but no matching bed available")

    return Recommendation(decision=decision, headline=headline, top_reasons=top, review_reasons=reasons,
                          hospital_questions=questions, tree=steps, held_bed=held, alternative_beds=alternatives)
