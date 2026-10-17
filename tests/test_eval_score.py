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


def test_error_and_no_truth_and_false_approve():
    assert outcome("?", "approve") == "no ground truth"
    assert outcome("deny", "error") == "ERROR"
    rows = [
        {"packet_id": "R04", "decision": "approve", "deciding_rules": [], "review_rules": [], "seconds": 1.0},
        {"packet_id": "R05", "decision": "error", "deciding_rules": [], "review_rules": [], "seconds": 0.0},
        {"packet_id": "R06", "decision": "deny", "deciding_rules": [], "review_rules": [], "seconds": 1.0},
    ]
    md = score(rows, {"R04": "deny", "R05": "approve"})
    assert "False approvals: 1" in md
    assert "| ERROR |" in md and "Errors: 1" in md
    assert "No ground truth: 1" in md


def test_load_truth_malformed_exits_with_clear_message(tmp_path, capsys):
    import pytest
    from eval.run import load_truth
    bad = tmp_path / "decisions.json"
    bad.write_text("{not json", "utf-8")
    with pytest.raises(SystemExit) as e:
        load_truth(bad, rows_path=tmp_path / "rows.json")
    assert e.value.code != 0
    assert "rows.json" in capsys.readouterr().err


def test_load_truth_ok(tmp_path):
    from eval.run import load_truth
    f = tmp_path / "d.json"
    f.write_text('{"packets": [{"packet_id": "R01", "decision": "deny"}]}', "utf-8")
    assert load_truth(f, rows_path=tmp_path / "rows.json") == {"R01": "deny"}


def test_persist_rows_writes_file(tmp_path):
    from eval.run import persist_rows
    p = persist_rows([{"packet_id": "R01"}], tmp_path / "eval")
    assert p.exists() and "R01" in p.read_text("utf-8")
