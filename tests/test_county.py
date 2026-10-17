import pytest

from app.rules.county import load_plan_map, suggest_county
from app.schemas import Evidence
from tests.factories import make_summary


def summ(plan, verified=True, clues=()):
    ev = [Evidence(page=4, quote=f"Health plan: {plan}", verified=verified)] if plan else []
    return make_summary(health_plan=plan, health_plan_evidence=ev,
                        location_clues=[Evidence(page=p, quote=q, verified=True) for p, q in clues])


@pytest.mark.parametrize("plan", ["CalOptima", "Cal Optima", "CALOPTIMA", "CalOptima Medi-Cal"])
def test_caloptima_variants_map_to_orange(plan):
    s = suggest_county(summ(plan))
    assert s.county == "Orange" and s.candidates == ["Orange"] and not s.confirmed
    assert "(p.4)" in s.basis and "Orange County" in s.basis and "not verified" not in s.basis


def test_la_care_with_punctuation():
    assert suggest_county(summ("L.A. Care Health Plan")).county == "Los Angeles"


def test_multi_county_plan_gives_candidates_only():
    s = suggest_county(summ("IEHP"))
    assert s.county is None and s.candidates == ["Riverside", "San Bernardino"]
    assert "several counties" in s.basis


def test_health_net_does_not_map():
    s = suggest_county(summ("Health Net"))
    assert s.county is None and s.candidates == [] and "does not map to a single county" in s.basis


def test_unknown_plan_does_not_map():
    assert suggest_county(summ("Acme Care")).county is None


def test_no_plan_never_uses_clues():
    s = suggest_county(summ(None, clues=[(2, "Hospital in Irvine, Orange County")]))
    assert s.county is None
    assert s.basis.startswith("No health plan named in the packet.")
    assert "location clues: 'Hospital in Irvine, Orange County' (p.2)" in s.basis


def test_unverified_plan_quote_is_flagged():
    s = suggest_county(summ("CalOptima", verified=False))
    assert s.county == "Orange" and s.basis.endswith("quote not verified")


def test_whole_word_match_only():
    assert suggest_county(summ("Kaiserlike")).county is None


def test_plan_map_is_editable_json():
    assert load_plan_map()["cal optima"] == ["Orange"]
