"""De-identified case profile used to match lessons to new cases."""
from app.schemas import AnalysisResult, CaseProfile, CaseSummary, Rule

FLAG_KINDS = ("must", "exclude", "conditional", "document")


def from_summary(summary: CaseSummary) -> CaseProfile:
    """Profile from the summary only (county, payer, bed needs). Used before rules are evaluated."""
    n = summary.bed_needs
    return CaseProfile(county=n.county, payer=summary.health_plan, sex=n.sex, wheelchair=n.wheelchair,
                       oxygen=n.oxygen, isolation=n.isolation, mobility_aid=n.mobility_aid)


def build_profile(result: AnalysisResult, rules: list[Rule]) -> CaseProfile:
    base = from_summary(result.summary)
    meta = {r.id: r for r in rules}
    systems, flagged = set(), set()
    for res in result.rule_results:
        rule = meta.get(res.rule_id)
        if rule is None:
            continue
        if rule.source == "by_system" and rule.kind == "eligible" and res.status == "pass":
            systems.add(rule.section.removesuffix(" (eligible)"))
        elif rule.kind in FLAG_KINDS and res.status in ("fail", "unknown"):
            flagged.add(rule.id)
    return base.model_copy(update={"systems": sorted(systems), "flagged_rule_ids": sorted(flagged)})
