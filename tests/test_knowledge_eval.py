import re

import pytest
from sqlmodel import Session, SQLModel

import app.knowledge.models  # noqa: F401
import app.workflow.models  # noqa: F401
from app.db import make_engine, set_engine
from app.rules.criteria import load_criteria
from app.schemas import CaseSummary, RuleGroupOutput, RuleResult, CaseSummaryExtract
from eval import run as eval_run
from tests.factories import make_packet, make_summary
from tests.fakes import FakeLLM
from tests.kn_helpers import approved


def handler(output, system, content):
    if output is CaseSummaryExtract:
        return make_summary()
    ids = re.findall(r"^- (\S+) \[", content[-1]["text"], re.M)
    return RuleGroupOutput(results=[RuleResult(rule_id=i, status="pass", reasoning="ok", confidence="high")
                                    for i in ids])


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    engine = make_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    set_engine(engine)

    async def fake_transcribe(llm, pdf, cache, concurrency=8, on_progress=None):
        return make_packet()
    monkeypatch.setattr(eval_run, "transcribe_packet", fake_transcribe)
    with Session(engine) as s:
        approved(s, "rule_interpretation", {"rule_id": "MOB-3", "ai_status": "pass", "coordinator_status": "fail"},
                 note="Cane users do not count")
    return tmp_path


async def run(tmp_path, **kw):
    llm = FakeLLM(handler)
    (tmp_path / "eval").mkdir(exist_ok=True)
    await eval_run.run_one(tmp_path / "R99.pdf", llm, load_criteria(), tmp_path / "eval", **kw)
    return "\n".join(c["text"] for call in llm.calls for c in call["content"])


async def test_run_one_uses_no_knowledge_by_default(env):
    assert "Coordinator guidance" not in await run(env) and "Lesson #" not in await run(env)


async def test_run_one_with_knowledge_adds_guidance(env):
    text = await run(env, with_knowledge=True)
    assert "Coordinator guidance from past cases" in text and "Cane users do not count" in text


def test_cli_flag_defaults_off():
    assert eval_run.build_parser().parse_args(["--packets-dir", "p", "--decisions", "d"]).with_knowledge is False
    assert eval_run.build_parser().parse_args(["--packets-dir", "p", "--decisions", "d",
                                               "--with-knowledge"]).with_knowledge is True
