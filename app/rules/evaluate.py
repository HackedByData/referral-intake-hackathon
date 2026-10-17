"""Case summary + parallel rule-group evaluation with Claude. Every rule gets exactly one result."""
import asyncio
import logging
import time

from app.llm import LLMError
from app.rules.criteria import Criteria
from app.rules.prompts import EVAL_SYSTEM, KNOWN_GAPS, RULES_TASK, SUMMARY_TASK
from app.schemas import CaseSummary, CaseSummaryExtract, PacketText, Rule, RuleGroupOutput, RuleResult

log = logging.getLogger(__name__)
SPLIT_THRESHOLD = 6


def packet_block(packet: PacketText, criteria: Criteria) -> dict:
    """Shared, cached prefix: identical bytes for every call on the same packet."""
    text = (f"<packet>\n{packet.to_markdown()}\n</packet>\n\n"
            f"<precedence>\n{criteria.precedence}\n</precedence>\n\n"
            f"<known_gaps>\n{KNOWN_GAPS}\n</known_gaps>")
    return {"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}


def _rule_lines(rules: list[Rule]) -> str:
    return "\n".join(f"- {r.id} [{r.kind}] ({r.section}): {r.text}" for r in rules)


async def summarize(llm, packet: PacketText, criteria: Criteria) -> CaseSummary:
    checklist = _rule_lines(criteria.checklist())
    content = [packet_block(packet, criteria), {"type": "text", "text": SUMMARY_TASK.format(checklist=checklist)}]
    extract = await llm.parse(system=EVAL_SYSTEM, content=content, output=CaseSummaryExtract, effort="high",
                              max_tokens=16000)
    return CaseSummary.model_validate(extract.model_dump())


def _ensure_coverage(rules: list[Rule], results: list[RuleResult]) -> list[RuleResult]:
    wanted = {r.id for r in rules}
    by_id: dict[str, RuleResult] = {}
    for r in results:
        if r.rule_id not in wanted:
            log.warning("Dropping result for unknown rule id %s", r.rule_id)
        elif r.rule_id not in by_id:
            by_id[r.rule_id] = r
    return [by_id.get(rule.id) or RuleResult(rule_id=rule.id, status="unknown", confidence="low",
                                             reasoning="Not evaluated by the model; check this rule by hand.")
            for rule in rules]


GUIDANCE_HEADER = ("\n\nCoordinator guidance from past cases (approved by IHH staff; use it to interpret the rule, "
                   "still cite this packet's evidence):\n")


def _guidance_text(rules: list[Rule], guidance: dict[str, list[str]] | None) -> str:
    lines = [f"- {r.id}: {line}" for r in rules for line in (guidance or {}).get(r.id, [])]
    return GUIDANCE_HEADER + "\n".join(lines) if lines else ""


async def evaluate_group(llm, packet: PacketText, criteria: Criteria, group: str, rules: list[Rule],
                         _depth: int = 0, guidance: dict[str, list[str]] | None = None) -> list[RuleResult]:
    # Guidance goes in the per-group task text ONLY: packet_block is the shared cached prefix and must not change.
    task = RULES_TASK.format(group=group, rules=_rule_lines(rules)) + _guidance_text(rules, guidance)
    content = [packet_block(packet, criteria), {"type": "text", "text": task}]
    try:
        out = await llm.parse(system=EVAL_SYSTEM, content=content, output=RuleGroupOutput,
                              effort="high", max_tokens=20000)
        return _ensure_coverage(rules, out.results)
    except LLMError as e:
        if len(rules) > SPLIT_THRESHOLD and _depth == 0:
            log.warning("Group %s failed (%s); splitting in half and retrying", group, e)
            mid = len(rules) // 2
            a, b = await asyncio.gather(
                evaluate_group(llm, packet, criteria, f"{group}-1", rules[:mid], _depth + 1, guidance),
                evaluate_group(llm, packet, criteria, f"{group}-2", rules[mid:], _depth + 1, guidance))
            return a + b
        log.error("Group %s failed: %s", group, e)
        return _ensure_coverage(rules, [])
    except Exception as e:  # rate limit, timeout, connection error: never cancel the other groups
        log.error("Group %s failed with %s: %s", group, type(e).__name__, e)
        return _ensure_coverage(rules, [])


def superseded_results(criteria: Criteria) -> list[RuleResult]:
    return [RuleResult(rule_id=r.id, status="not_applicable", confidence="high",
                       reasoning=f"Superseded by {r.superseded_by}: IHH's internal admission criteria win over the referral form.")
            for r in criteria.superseded()]


async def evaluate_rules(llm, packet: PacketText, criteria: Criteria, concurrency: int = 8,
                         guidance: dict[str, list[str]] | None = None) -> list[RuleResult]:
    sem = asyncio.Semaphore(concurrency)

    async def run(group: str, rules: list[Rule]) -> list[RuleResult]:
        async with sem:
            return await evaluate_group(llm, packet, criteria, group, rules, guidance=guidance)

    groups = criteria.groups()
    chunks = await asyncio.gather(*(run(g, rules) for g, rules in groups.items() if rules))
    return [r for chunk in chunks for r in chunk] + superseded_results(criteria)


async def evaluate_packet(llm, packet: PacketText, criteria: Criteria, concurrency: int = 8,
                          guidance: dict[str, list[str]] | None = None
                          ) -> tuple[CaseSummary, list[RuleResult], dict[str, float]]:
    t0 = time.perf_counter()
    summary = await summarize(llm, packet, criteria)  # first: warms the prompt cache
    t1 = time.perf_counter()
    results = await evaluate_rules(llm, packet, criteria, concurrency, guidance)
    return summary, results, {"summary": t1 - t0, "rules": time.perf_counter() - t1}
