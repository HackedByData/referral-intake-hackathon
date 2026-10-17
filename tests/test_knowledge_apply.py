import re

from app.beds import service
from app.beds.models import Bed, Site
from app.knowledge import apply, store
from app.rules.county import normalize, suggest_county
from app.rules.criteria import load_criteria
from app.rules.evaluate import evaluate_group, evaluate_rules, packet_block
from app.schemas import CaseProfile, CaseSummary, LessonReview, RuleGroupOutput, RuleResult
from tests.factories import make_packet, make_rule, make_summary
from tests.fakes import FakeLLM
from tests.kn_helpers import PROFILE, approved, bed_payload, session  # noqa: F401


def add_beds(s):
    for sid in ("sa", "sb", "sc"):
        s.add(Site(id=sid, name=f"Facility {sid}", county="Orange" if sid != "sc" else "Fresno",
                   program_type="recuperative_care"))
    s.add_all([Bed(id="A1", site_id="sa", room="1", label="A", sex_designation="male"),
               Bed(id="B1", site_id="sb", room="1", label="A", sex_designation="male"),
               Bed(id="C1", site_id="sc", room="1", label="A", sex_designation="male")])
    s.commit()


def options(s, ids):
    return [service.bed_option(s, s.get(Bed, i), ["male room"]) for i in ids]


# ---------- bed re-ranking ----------
def test_approved_bed_preference_boosts_chosen_facility_without_adding_options(session):
    add_beds(session)
    approved(session, "bed_preference", bed_payload("sa", "sb"))
    opts = options(session, ["A1", "B1"])  # C1 (Fresno) was filtered out by find_matches upstream
    out, used = apply.rerank_beds(session, opts, PROFILE)
    assert [o.bed_id for o in out] == ["B1", "A1"]
    assert {o.bed_id for o in out} == {"A1", "B1"}
    assert any("Preferred for similar cases (lesson #1)" in n for n in out[0].match_notes)
    assert used == [1]
    assert out[1].match_notes == ["male room"]


def test_chosen_facility_absent_from_options_is_never_introduced(session):
    add_beds(session)
    approved(session, "bed_preference", bed_payload("sa", "sc"))
    out, _ = apply.rerank_beds(session, options(session, ["A1", "B1"]), PROFILE)
    assert {o.bed_id for o in out} == {"A1", "B1"}
    assert out[0].bed_id == "B1"  # held facility is demoted, order otherwise stable


def test_proposed_rejected_retired_lessons_are_ignored(session):
    add_beds(session)
    prop = store.create(session, "bed_preference", PROFILE, bed_payload("sa", "sb"), "n", "c", "R1")
    rej = store.create(session, "bed_preference", PROFILE, bed_payload("sa", "sb"), "n", "c", "R1")
    store.review(session, rej.id, "reject", LessonReview(reviewer="s"))
    ret = approved(session, "bed_preference", bed_payload("sa", "sb"))
    store.review(session, ret.id, "retire", LessonReview(reviewer="s"))
    rule = store.create(session, "rule_interpretation", PROFILE,
                        {"rule_id": "MOB-3", "ai_status": "pass", "coordinator_status": "fail"}, "n", "c", "R1")
    county = store.create(session, "county_mapping", PROFILE,
                          {"health_plan": "Acme", "ai_county": None, "coordinator_county": "Kern"}, "", "c", "R1")
    prec = store.create(session, "decision_precedent", PROFILE,
                        {"ai_decision": "approve", "coordinator_decision": "deny"}, "n", "c", "R1")
    assert prop.id and rule.id and county.id and prec.id
    opts = options(session, ["A1", "B1"])
    out, used = apply.rerank_beds(session, opts, PROFILE)
    assert out == opts and used == []
    assert apply.rule_guidance(session, [make_rule("MOB-3")], PROFILE) == ({}, [])
    assert apply.plan_map_additions(session) == ({}, {})
    assert apply.precedents(session, PROFILE) == []


def test_low_similarity_bed_preference_does_not_apply(session):
    add_beds(session)
    other = CaseProfile(county="Fresno", payer="Other", sex="female", wheelchair=True, oxygen=True, isolation=True)
    approved(session, "bed_preference", bed_payload("sa", "sb"), profile=other)
    opts = options(session, ["A1", "B1"])
    assert apply.rerank_beds(session, opts, PROFILE) == (opts, [])


# ---------- rule guidance ----------
def test_rule_guidance_lines_best_first_max_three(session):
    for i in range(4):
        approved(session, "rule_interpretation",
                 {"rule_id": "MOB-3", "ai_status": "pass", "coordinator_status": "fail"}, note=f"note {i}")
    approved(session, "rule_interpretation", {"rule_id": "ADL-1", "ai_status": "unknown", "coordinator_status": "pass"},
             note="")
    guidance, used = apply.rule_guidance(session, [make_rule("MOB-3"), make_rule("ADL-1")], PROFILE)
    assert len(guidance["MOB-3"]) == 3
    assert guidance["MOB-3"][0] == "Lesson #1: AI said pass, coordinator decided fail. Coordinator note: note 0"
    assert guidance["ADL-1"] == ["Lesson #5: AI said unknown, coordinator decided pass. Coordinator note: (none)"]
    assert used == [1, 2, 3, 5]


def test_rule_guidance_in_group_task_text_not_in_packet_block():
    rules = [make_rule("MOB-3"), make_rule("MOB-4")]
    seen = []

    def handler(output, system, content):
        seen.append(content)
        return RuleGroupOutput(results=[RuleResult(rule_id=r.id, status="pass", reasoning="ok", confidence="high")
                                        for r in rules])
    import asyncio
    packet, criteria = make_packet(), load_criteria()
    before = packet_block(packet, criteria)
    guidance = {"MOB-3": ["Lesson #7: AI said pass, coordinator decided fail. Coordinator note: cane"]}
    asyncio.run(evaluate_group(FakeLLM(handler), packet, criteria, "function", rules, guidance=guidance))
    content = seen[0]
    assert content[0] == before  # cached prefix byte-identical
    assert "Lesson #7" not in content[0]["text"]
    task = content[-1]["text"]
    assert "Coordinator guidance from past cases" in task and "- MOB-3: Lesson #7:" in task
    assert "MOB-4: Lesson" not in task


def test_no_guidance_leaves_task_text_unchanged():
    import asyncio
    seen = []

    def handler(output, system, content):
        seen.append(content[-1]["text"])
        return RuleGroupOutput(results=[])
    rules = [make_rule("MOB-3")]
    asyncio.run(evaluate_group(FakeLLM(handler), make_packet(), load_criteria(), "function", rules))
    asyncio.run(evaluate_group(FakeLLM(handler), make_packet(), load_criteria(), "function", rules, guidance={}))
    assert seen[0] == seen[1] and "guidance" not in seen[0].lower()


def test_evaluate_rules_passes_guidance_to_matching_groups_only():
    import asyncio
    criteria = load_criteria()
    tasks = []

    def handler(output, system, content):
        tasks.append(content[-1]["text"])
        ids = re.findall(r"^- (\S+) \[", content[-1]["text"], re.M)
        return RuleGroupOutput(results=[RuleResult(rule_id=i, status="pass", reasoning="ok", confidence="high")
                                        for i in ids])
    asyncio.run(evaluate_rules(FakeLLM(handler), make_packet(), criteria,
                               guidance={"MOB-3": ["Lesson #1: AI said pass, coordinator decided fail. Coordinator note: x"]}))
    with_guidance = [t for t in tasks if "Coordinator guidance" in t]
    assert len(with_guidance) == 1 and "- MOB-3 [" in with_guidance[0]


# ---------- county ----------
def test_county_addition_makes_suggest_county_return_learned_county(session):
    approved(session, "county_mapping", {"health_plan": "Acme Health Plan", "ai_county": None,
                                         "coordinator_county": "Kern"})
    additions, ids = apply.plan_map_additions(session)
    assert additions == {"acme health plan": ["Kern"]} and ids == {"acme health plan": 1}
    from app.rules.county import load_plan_map, merge_plan_map
    s = suggest_county(make_summary(health_plan="Acme Health Plan Medi-Cal"), merge_plan_map(load_plan_map(), additions),
                       learned=ids)
    assert s.county == "Kern" and "learned from lesson #1" in s.basis


def test_learned_county_overrides_builtin_mapping(session):
    approved(session, "county_mapping", {"health_plan": "CalOptima", "ai_county": "Orange",
                                         "coordinator_county": "Riverside"})
    from app.rules.county import learned_lesson_id, load_plan_map, merge_plan_map
    additions, ids = apply.plan_map_additions(session)
    merged = merge_plan_map(load_plan_map(), additions)
    summary = make_summary(health_plan="CalOptima")
    s = suggest_county(summary, merged, learned=ids)
    assert s.county == "Riverside" and "learned from lesson #1" in s.basis
    assert learned_lesson_id(summary, merged, ids) == 1
    assert learned_lesson_id(make_summary(health_plan="IEHP"), merged, ids) is None
    assert normalize("x") == "x"


def test_builtin_county_basis_unchanged_without_learning():
    s = suggest_county(make_summary(health_plan="CalOptima"))
    assert s.county == "Orange" and "learned" not in s.basis


# ---------- precedents ----------
def test_precedents_threshold_and_order(session):
    pay = {"ai_decision": "approve", "coordinator_decision": "deny", "ai_rule_ids": []}
    approved(session, "decision_precedent", pay, note="close")
    far = CaseProfile(county="Fresno", payer="Other", sex="female", wheelchair=True, oxygen=True, isolation=True)
    approved(session, "decision_precedent", pay, profile=far, note="far")
    out = apply.precedents(session, PROFILE)
    assert [p.lesson_id for p in out] == [1]
    assert out[0].similarity == 1.0 and out[0].ai_decision == "approve" and out[0].coordinator_decision == "deny"
    assert out[0].note == "close" and out[0].summary


def test_precedents_limit(session):
    for _ in range(5):
        approved(session, "decision_precedent", {"ai_decision": "approve", "coordinator_decision": "deny"})
    assert len(apply.precedents(session, PROFILE)) == 3
