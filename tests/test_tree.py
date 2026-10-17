from app.rules.tree import decide
from app.schemas import Conflict, Evidence
from tests.factories import make_bed_option, make_result, make_rule, make_summary

RULES = [
    make_rule("GEN-1"), make_rule("GEN-2"), make_rule("GEN-3"),
    make_rule("MOB-3", "must"), make_rule("MH-2", "exclude"),
    make_rule("OTH-2", "note", human_review=True), make_rule("OTH-3", "conditional"),
    make_rule("DOC-6", "document", source="checklist"), make_rule("DM-Q1", "ask"),
]
BEDS = [make_bed_option("B1"), make_bed_option("B2")]


def results(**status):
    """Default: everything passes. Override with RULE_ID=status (use _ for -: MH_2='fail')."""
    base = {"GEN-1": "pass", "GEN-2": "fail", "GEN-3": "fail", "MOB-3": "pass", "MH-2": "pass",
            "OTH-2": "not_applicable", "OTH-3": "not_applicable", "DOC-6": "pass", "DM-Q1": "not_applicable"}
    base.update({k.replace("_", "-"): v for k, v in status.items()})
    return [make_result(k, v) for k, v in base.items()]


def node(rec, name):
    return next(s for s in rec.tree if s.node == name)


def test_all_pass_approves_and_holds_first_bed():
    rec = decide(RULES, results(), make_summary(), BEDS)
    assert rec.decision == "approve"
    assert rec.held_bed.bed_id == "B1" and [b.bed_id for b in rec.alternative_beds] == ["B2"]
    assert rec.headline.startswith("Recommend approve")
    assert all(s.outcome == "clear" for s in rec.tree if s.node != "bed")


def test_verified_exclusion_denies():
    rec = decide(RULES, results(MH_2="fail"), make_summary(), BEDS)
    assert rec.decision == "deny" and node(rec, "exclusions").rule_ids == ["MH-2"]
    assert rec.held_bed is None
    assert any("[MH-2]" in r for r in rec.top_reasons)


def test_unverified_exclusion_needs_review_not_deny():
    res = results()
    res[4] = make_result("MH-2", "fail", verified=False)
    rec = decide(RULES, res, make_summary(), BEDS)
    assert rec.decision == "needs_review"
    assert any(r.kind == "unverified_evidence" and r.rule_ids == ["MH-2"] for r in rec.review_reasons)


def test_must_fail_denies():
    rec = decide(RULES, results(MOB_3="fail"), make_summary(), BEDS)
    assert rec.decision == "deny" and node(rec, "must_rules").rule_ids == ["MOB-3"]


def test_unknown_must_needs_review_with_hospital_question():
    res = results()
    res[3] = make_result("MOB-3", "unknown", question="How far can the patient walk?")
    rec = decide(RULES, res, make_summary(), BEDS)
    assert rec.decision == "needs_review"
    assert rec.hospital_questions[0].question == "How far can the patient walk?"
    assert rec.held_bed.bed_id == "B1"  # bed stays held while info is gathered


def test_unknown_without_question_gets_default_from_rule_text():
    res = results()
    res[3] = make_result("MOB-3", "unknown")
    rec = decide(RULES, res, make_summary(), BEDS)
    assert "Text of MOB-3" in rec.hospital_questions[0].question


def test_human_review_rule_never_denies():
    rec = decide(RULES, results(OTH_2="fail"), make_summary(), BEDS)
    assert rec.decision == "needs_review"
    assert any(r.kind == "judgment_call" for r in rec.review_reasons)
    rec2 = decide(RULES, results(OTH_2="needs_human"), make_summary(), BEDS)
    assert rec2.decision == "needs_review"


def test_general_eligibility_all_fail_denies_unknown_reviews():
    rec = decide(RULES, results(GEN_1="fail"), make_summary(), BEDS)
    assert rec.decision == "deny" and node(rec, "general_eligibility").outcome == "triggered"
    rec2 = decide(RULES, results(GEN_1="unknown"), make_summary(), BEDS)
    assert rec2.decision == "needs_review"


def test_missing_document_needs_review():
    rec = decide(RULES, results(DOC_6="fail"), make_summary(), BEDS)
    assert rec.decision == "needs_review"
    assert any(r.kind == "missing_info" and "DOC-6" in r.rule_ids for r in rec.review_reasons)


def test_conditional_unknown_needs_review():
    rec = decide(RULES, results(OTH_3="unknown"), make_summary(), BEDS)
    assert rec.decision == "needs_review"


def test_ask_rules_never_block():
    rec = decide(RULES, results(DM_Q1="unknown"), make_summary(), BEDS)
    assert rec.decision == "approve"


def test_approve_without_bed_stays_approve_with_no_bed_reason():
    rec = decide(RULES, results(), make_summary(), [])
    assert rec.decision == "approve" and rec.held_bed is None
    assert any(r.kind == "no_bed" for r in rec.review_reasons)
    assert "no matching bed" in rec.headline


def test_conflict_on_flagged_rule_adds_reason_and_ignored_otherwise():
    conflict = Conflict(topic="Walking distance", statements=[Evidence(page=1, quote="a"), Evidence(page=2, quote="b")],
                        related_rule_ids=["MOB-3"], why_it_matters="Mobility rule")
    res = results()
    res[3] = make_result("MOB-3", "unknown")
    rec = decide(RULES, res, make_summary(conflicts=[conflict]), BEDS)
    assert any(r.kind == "conflict" for r in rec.review_reasons)
    rec2 = decide(RULES, results(), make_summary(conflicts=[conflict]), BEDS)
    assert rec2.decision == "approve"


def test_missing_result_for_rule_is_treated_as_not_evaluated():
    res = [r for r in results() if r.rule_id != "MH-2"]
    rec = decide(RULES, res, make_summary(), BEDS)
    assert rec.decision == "needs_review"


def test_must_pass_without_verified_evidence_needs_review():
    res = results()
    res[3] = make_result("MOB-3", "pass", verified=False)
    rec = decide(RULES, res, make_summary(), BEDS)
    assert rec.decision == "needs_review"
    assert any(r.kind == "unverified_evidence" and r.rule_ids == ["MOB-3"] for r in rec.review_reasons)
    assert "MOB-3" in node(rec, "unverified").rule_ids


def test_unverified_general_pass_needs_review():
    res = results()
    res[0] = make_result("GEN-1", "pass", verified=False)
    rec = decide(RULES, res, make_summary(), BEDS)
    assert rec.decision == "needs_review"
    assert any(r.kind == "unverified_evidence" and r.rule_ids == ["GEN-1"] for r in rec.review_reasons)


def test_unverified_exclude_pass_still_approves():
    res = results()
    res[4] = make_result("MH-2", "pass", verified=False)
    rec = decide(RULES, res, make_summary(), BEDS)
    assert rec.decision == "approve"


def _with_text(rule, text):
    return rule.model_copy(update={"text": text})


def test_unconditional_must_not_applicable_needs_review():
    rules = RULES + [make_rule("ADL-1", "must")]
    rs = results() + [make_result("ADL-1", "not_applicable")]
    rec = decide(rules, rs, make_summary(), BEDS)
    assert rec.decision == "needs_review"
    rr = [r for r in rec.review_reasons if r.rule_ids == ["ADL-1"]]
    assert rr and rr[0].kind == "unverified_evidence" and "always applies" in rr[0].detail
    assert node(rec, "unverified").rule_ids == ["ADL-1"]


def test_conditional_text_must_not_applicable_still_approves():
    rules = RULES + [_with_text(make_rule("DME-1", "must"), "If using DME, must have it")]
    rs = results() + [make_result("DME-1", "not_applicable")]
    assert decide(rules, rs, make_summary(), BEDS).decision == "approve"


def test_mob3_and_mob4_both_not_applicable_needs_review():
    rules = [_with_text(r, "If ambulatory, walks 150 ft") if r.id == "MOB-3" else r for r in RULES]
    rules.append(_with_text(make_rule("MOB-4", "must"), "If wheelchair, must transfer"))
    rs = results(MOB_3="not_applicable") + [make_result("MOB-4", "not_applicable")]
    rec = decide(rules, rs, make_summary(), BEDS)
    assert rec.decision == "needs_review"
    assert any(r.rule_ids == ["MOB-3", "MOB-4"] and r.detail == "Neither mobility rule was applied."
               for r in rec.review_reasons)


def test_deny_headline_grammar():
    rec = decide(RULES, results(MH_2="fail"), make_summary(), BEDS)
    assert rec.headline == "Recommend deny: 1 criterion not met"
    rec2 = decide(RULES, results(MH_2="fail", MOB_3="fail"), make_summary(), BEDS)
    assert rec2.headline == "Recommend deny: 2 criteria not met"
