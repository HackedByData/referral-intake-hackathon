from app.schemas import CoordinatorDecision, RuleGroupOutput
from tests.factories import make_analysis, make_packet


def test_packet_to_markdown_has_page_headings():
    md = make_packet().to_markdown()
    assert md.startswith("## Page 1 — other")
    assert "## Page 3 — other" in md
    assert "ambulated 150 ft" in md


def test_analysis_round_trips_json():
    a = make_analysis()
    assert type(a).model_validate_json(a.model_dump_json()) == a


def test_rule_group_output_schema_is_strict_compatible():
    schema = RuleGroupOutput.model_json_schema()
    assert "results" in schema["properties"]


def test_coordinator_decision_defaults():
    d = CoordinatorDecision(decision="approve", coordinator="Test Coordinator")
    assert d.rule_overrides == [] and d.bed_id is None
