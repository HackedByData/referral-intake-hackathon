from collections import Counter

from app.rules.criteria import GROUP_NAMES, load_criteria


def test_counts_match_source():
    c = load_criteria()
    by_source = Counter(r.source for r in c.rules)
    assert by_source == {"outline": 51, "by_system": 81, "checklist": 10, "form": 13}
    assert len(c.rules) == 155
    assert len({r.id for r in c.rules}) == 155  # IDs unique


def test_kinds():
    c = load_criteria().by_id()
    assert c["GEN-1"].kind == "must" and c["GEN-1"].group == "function"
    assert c["MH-2"].kind == "exclude"
    assert c["DOC-6"].kind == "document" and c["DOC-6"].group == "infection_docs"
    assert c["OTH-2"].human_review is True
    assert all(r.kind == "exclude" for r in load_criteria().rules if r.section.endswith("(ineligible)"))


def test_superseded_form_rules_are_not_evaluated():
    c = load_criteria()
    assert {r.id for r in c.superseded()} == {"FORM-M6", "FORM-X3", "FORM-X6"}
    evaluated = {r.id for rules in c.groups().values() for r in rules}
    assert "FORM-M6" not in evaluated and "FORM-X4" in evaluated


def test_every_evaluable_rule_is_in_exactly_one_named_group():
    groups = load_criteria().groups()
    assert set(groups) == set(GROUP_NAMES)
    ids = [r.id for rules in groups.values() for r in rules]
    assert len(ids) == len(set(ids)) == 152
    assert all(len(rules) <= 30 for rules in groups.values())
