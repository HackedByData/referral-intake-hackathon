"""The case-summary output schema sent to Claude must stay small and model-fillable.

Live finding (2026-10-17): the API rejected CaseSummary with 400 "Schema is too complex." once system-filled
fields (suggested_county, bed_needs.county) were added: 13 optional unions were rejected, 9 accepted.
"""
import json

from anthropic.lib._parse._transform import transform_schema

from app.rules.criteria import load_criteria
from app.rules.evaluate import summarize
from app.schemas import BedNeeds, CaseSummary
from tests.factories import make_packet
from tests.fakes import FakeLLM

MAX_OPTIONAL_UNIONS = 9  # measured against the live API; keep the model-facing schema at or below this


def _fill(output_type):
    return output_type(
        one_liner="Adult referred after inpatient stay.", sex="male", has_ihh_referral_form=True,
        bed_needs={"sex": "male", "wheelchair": False},
    )


async def test_summarize_asks_the_model_only_for_model_fillable_fields():
    llm = FakeLLM(lambda output, system, content: _fill(output))
    await summarize(llm, make_packet(), load_criteria())
    schema = llm.calls[0]["output"].model_json_schema()
    text = json.dumps(schema)
    assert "suggested_county" not in text
    needs = schema["$defs"][next(k for k in schema["$defs"] if "Needs" in k)]
    assert "county" not in needs["properties"]


async def test_summarize_returns_a_full_case_summary_with_system_fields_empty():
    llm = FakeLLM(lambda output, system, content: _fill(output))
    summary = await summarize(llm, make_packet(), load_criteria())
    assert isinstance(summary, CaseSummary) and isinstance(summary.bed_needs, BedNeeds)
    assert summary.suggested_county is None and summary.bed_needs.county is None
    assert summary.one_liner.startswith("Adult")


async def test_model_facing_summary_schema_stays_within_complexity_budget():
    llm = FakeLLM(lambda output, system, content: _fill(output))
    await summarize(llm, make_packet(), load_criteria())
    sent = json.dumps(transform_schema(llm.calls[0]["output"].model_json_schema()))
    assert sent.count('"anyOf"') <= MAX_OPTIONAL_UNIONS
