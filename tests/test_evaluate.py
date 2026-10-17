import re

from app.llm import LLMError
from app.rules.criteria import load_criteria
from app.rules.evaluate import evaluate_group, evaluate_packet, superseded_results
from app.schemas import CaseSummaryExtract, RuleGroupOutput, RuleResult
from tests.factories import make_packet, make_rule, make_summary
from tests.fakes import FakeLLM

RULE_LINE = re.compile(r"^- (\S+) \[", re.M)


def ids_in(content) -> list[str]:
    return RULE_LINE.findall(content[-1]["text"])


def happy(output, system, content):
    if output is CaseSummaryExtract:
        return make_summary()
    return RuleGroupOutput(results=[RuleResult(rule_id=i, status="pass", reasoning="ok", confidence="high")
                                    for i in ids_in(content)])


async def test_evaluate_packet_covers_every_rule_once():
    criteria = load_criteria()
    llm = FakeLLM(happy)
    summary, results, timings = await evaluate_packet(llm, make_packet(), criteria)
    ids = [r.rule_id for r in results]
    assert len(ids) == len(set(ids)) == 155
    assert summary.sex == "male"
    assert {"summary", "rules"} <= set(timings)
    sup = {r.rule_id: r for r in results if r.rule_id in ("FORM-M6", "FORM-X3", "FORM-X6")}
    assert all(r.status == "not_applicable" and "Superseded" in r.reasoning for r in sup.values())


async def test_summary_runs_first_and_packet_block_is_cached():
    llm = FakeLLM(happy)
    await evaluate_packet(llm, make_packet(), load_criteria())
    assert llm.calls[0]["output"] is CaseSummaryExtract
    first_block = llm.calls[0]["content"][0]
    assert first_block["cache_control"] == {"type": "ephemeral"}
    assert all(c["content"][0]["text"] == first_block["text"] for c in llm.calls)  # identical cached prefix
    assert all(c["system"] == llm.calls[0]["system"] for c in llm.calls)


async def test_missing_and_unknown_ids_are_handled():
    rules = [make_rule("A-1"), make_rule("A-2")]

    def handler(output, system, content):
        return RuleGroupOutput(results=[
            RuleResult(rule_id="A-1", status="pass", reasoning="ok", confidence="high"),
            RuleResult(rule_id="ZZZ-9", status="fail", reasoning="made up", confidence="high"),
        ])

    out = await evaluate_group(FakeLLM(handler), make_packet(), load_criteria(), "function", rules)
    assert [r.rule_id for r in out] == ["A-1", "A-2"]
    assert out[1].status == "unknown" and out[1].confidence == "low"


async def test_failed_large_group_is_split_and_retried():
    rules = [make_rule(f"A-{i}") for i in range(10)]

    def handler(output, system, content):
        ids = ids_in(content)
        if len(ids) == 10:
            return LLMError("Model output was truncated (max_tokens)")
        return RuleGroupOutput(results=[RuleResult(rule_id=i, status="pass", reasoning="ok", confidence="high")
                                        for i in ids])

    out = await evaluate_group(FakeLLM(handler), make_packet(), load_criteria(), "function", rules)
    assert all(r.status == "pass" for r in out) and len(out) == 10


async def test_failed_small_group_becomes_unknown():
    rules = [make_rule("A-1")]
    out = await evaluate_group(FakeLLM(lambda *a: LLMError("x")), make_packet(), load_criteria(), "function", rules)
    assert out[0].status == "unknown"


def test_superseded_results():
    out = superseded_results(load_criteria())
    assert {r.rule_id for r in out} == {"FORM-M6", "FORM-X3", "FORM-X6"}


async def test_non_llm_error_becomes_unknown_not_crash():
    rules = [make_rule(f"A-{i}") for i in range(10)]
    out = await evaluate_group(FakeLLM(lambda *a: RuntimeError("rate limited")), make_packet(),
                               load_criteria(), "function", rules)
    assert len(out) == 10 and all(r.status == "unknown" for r in out)
