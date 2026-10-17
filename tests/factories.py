"""Builders for INVENTED test data. Never put real packet text here."""
from app.schemas import (
    AnalysisResult, BedNeeds, BedOption, CaseSummary, Evidence, PacketText, PageMarkdown,
    Recommendation, Rule, RuleResult,
)

PAGE_1 = """IHH RECUPERATIVE CARE REFERRAL FORM
Patient: [REDACTED]  Age: 54  Gender: Male
Housing: currently unhoused
Chief complaint: cellulitis of left lower leg
Ambulatory: Yes  Assistive device: front-wheeled walker
Self-administers all medications: Yes"""

PAGE_2 = """PHYSICAL THERAPY EVALUATION
Patient ambulated 150 ft with front wheeled walker, modified independent.
Transfers: independent. Alert and oriented x4.
Patient denies suicidal ideation."""

PAGE_3 = """PROGRESS NOTE
MRSA nares swab negative. Plan: oral antibiotics, discharge when stable."""


def make_packet(pages: list[str] | None = None) -> PacketText:
    texts = pages or [PAGE_1, PAGE_2, PAGE_3]
    return PacketText(
        packet_hash="testhash",
        page_count=len(texts),
        pages=[
            PageMarkdown(page=i + 1, doc_type="other", orientation="upright", legibility="good", markdown=t)
            for i, t in enumerate(texts)
        ],
    )


def make_rule(rule_id: str, kind: str = "must", section: str = "Test", group: str = "function",
              human_review: bool = False, source: str = "outline") -> Rule:
    return Rule(id=rule_id, source=source, section=section, kind=kind, text=f"Text of {rule_id}",
                group=group, human_review=human_review)


def make_result(rule_id: str, status: str = "pass", verified: bool = True, page: int = 2,
                quote: str = "ambulated 150 ft", question: str | None = None) -> RuleResult:
    ev = [Evidence(page=page, quote=quote, verified=verified)] if status in ("pass", "fail") else []
    return RuleResult(rule_id=rule_id, status=status, reasoning=f"Reasoning for {rule_id}.",
                      evidence=ev, hospital_question=question, confidence="high")


def make_summary(**overrides) -> CaseSummary:
    base = dict(
        one_liner="Adult man referred after inpatient stay for left-leg cellulitis; walks with a walker.",
        age="54", sex="male", admitting_diagnosis="cellulitis", housing_situation="unhoused",
        has_ihh_referral_form=True, documents_present=[], key_findings=[], conflicts=[],
        bed_needs=BedNeeds(sex="male", wheelchair=False, mobility_aid="walker", oxygen=False, isolation=False,
                           county="Orange"),
    )
    base.update(overrides)
    return CaseSummary(**base)


def make_bed_option(bed_id: str = "SA-101-A") -> BedOption:
    return BedOption(bed_id=bed_id, site="Site A (demo)", room="101", label="A", match_notes=["male room"])


def make_analysis(decision: str = "approve", held_bed: BedOption | None = None) -> AnalysisResult:
    rec = Recommendation(
        decision=decision, headline=f"Recommend {decision}", top_reasons=["Walks 150 ft (p.2) [MOB-3]"],
        review_reasons=[], hospital_questions=[], tree=[], held_bed=held_bed, alternative_beds=[],
    )
    return AnalysisResult(
        prompt_version="test", model="fake-model", packet=make_packet(), summary=make_summary(),
        rule_results=[make_result("MOB-3")], recommendation=rec, timings={"total": 1.0},
    )
