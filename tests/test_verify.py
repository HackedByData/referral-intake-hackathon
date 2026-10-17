from app.rules.verify import quote_on_page, verify_results, verify_summary
from app.schemas import Conflict, Evidence
from tests.factories import make_packet, make_result, make_summary


def test_exact_and_normalized_match():
    page = "Patient ambulated 150 ft with front wheeled walker, modified independent."
    assert quote_on_page("ambulated 150 ft with front wheeled walker", page)
    assert quote_on_page("AMBULATED   150 ft with front-wheeled walker", page)


def test_fuzzy_tolerates_small_ocr_differences():
    page = "MRSA nares swab negative. Plan: oral antibiotics"
    assert quote_on_page("MRSA nares swab negatlve", page)


def test_fabricated_quote_fails():
    page = "MRSA nares swab negative."
    assert not quote_on_page("patient on isolation precautions for C. diff", page)


def test_short_quote_needs_exact_match():
    assert not quote_on_page("AOx3", "Alert and oriented x4")


def test_off_by_one_page_is_corrected():
    packet = make_packet()
    r = make_result("MOB-3", quote="ambulated 150 ft", page=3, verified=False)  # really on page 2
    out = verify_results([r], packet)[0]
    assert out.evidence[0].verified is True and out.evidence[0].page == 2


def test_page_out_of_range_is_unverified_not_crash():
    packet = make_packet()
    r = make_result("MOB-3", quote="ambulated 150 ft", page=99, verified=True)
    out = verify_results([r], packet)[0]
    assert out.evidence[0].verified is False and out.evidence[0].page == 99


def test_llm_supplied_verified_flag_is_ignored():
    packet = make_packet()
    r = make_result("MH-2", quote="patient reports command hallucinations", page=2, verified=True)
    assert verify_results([r], packet)[0].evidence[0].verified is False


def test_verify_summary_conflicts():
    packet = make_packet()
    s = make_summary(conflicts=[Conflict(topic="Age", statements=[Evidence(page=1, quote="Age: 54"),
                                                                    Evidence(page=2, quote="Age: 61")],
                                         related_rule_ids=[], why_it_matters="")])
    st = verify_summary(s, packet).conflicts[0].statements
    assert st[0].verified is True and st[1].verified is False


def test_fuzzy_rejects_flipped_number():
    assert not quote_on_page("Walks 150 ft with front wheeled walker", "Walks 15 ft with front wheeled walker today")


def test_fuzzy_rejects_flipped_negation():
    assert not quote_on_page("patient is not suicidal today", "patient is suicidal today per note")


PCT_PAGE = "Vitals: O2 sat 88% on room air. Patient ambulated 15 ft with walker. Denies chest pain."


def test_fuzzy_rejects_flipped_percentage():
    assert not quote_on_page("O2 sat 98% on room air", PCT_PAGE)


def test_fuzzy_rejects_number_glued_to_unit():
    assert not quote_on_page("ambulated 150ft with walker", PCT_PAGE)


def test_fuzzy_rejects_number_with_trailing_period():
    assert not quote_on_page("O2 sat was 94%. on room air", PCT_PAGE)


def test_fuzzy_negation_with_trailing_period_must_survive():
    assert not quote_on_page("Patient reports no chest pain at all.", "Patient reports chest pain at all today")
    assert quote_on_page("Patient denies chest pain.", "Patient denies chest pain. Plan follow up")
