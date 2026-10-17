from eval.run import outcome, score


def test_outcome_labels():
    assert outcome("approve", "approve") == "match"
    assert outcome("deny", "approve") == "FALSE APPROVE"
    assert outcome("approve", "deny") == "false deny"
    assert outcome("deny", "needs_review") == "review (IHH denied)"


def test_score_table_has_no_quotes_and_totals():
    rows = [
        {"packet_id": "R01", "decision": "deny", "deciding_rules": ["MOB-3"], "review_rules": [], "seconds": 90.0},
        {"packet_id": "R02", "decision": "deny", "deciding_rules": ["MH-2"], "review_rules": [], "seconds": 80.0},
        {"packet_id": "R03", "decision": "needs_review", "deciding_rules": [], "review_rules": ["INF-1"], "seconds": 70.0},
    ]
    md = score(rows, {"R01": "deny", "R02": "approve", "R03": "approve"})
    assert "| R01 | deny | deny | match | MOB-3 |" in md
    assert "False approvals: 0" in md and "False denials: 1" in md and "Needs review: 1" in md
