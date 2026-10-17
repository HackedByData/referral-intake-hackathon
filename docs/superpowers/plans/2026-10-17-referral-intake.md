# Referral Intake + Bed Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** FastAPI backend that turns an uploaded image-only referral PDF into a cited, rule-by-rule eligibility recommendation with a held bed, lets a coordinator approve/deny/request info, and manages beds.

**Architecture:** Claude vision transcribes each page into markdown. Claude then evaluates every IHH rule ID in parallel groups, returning structured `RuleResult`s with page and quote evidence. Deterministic Python verifies the quotes and walks a decision tree. A SQLite bed management module matches and holds a bed. Coordinator decisions are transactional, audited, and write a mock Kipu payload.

**Tech Stack:** Python ≥3.12 (dev box: 3.14), FastAPI, SQLModel/SQLite, PyMuPDF, anthropic SDK ≥1.15 (`AsyncAnthropic`, `beta.messages.parse`), rapidfuzz, openpyxl, pytest + pytest-asyncio.

**Spec:** [`docs/superpowers/specs/2026-10-17-referral-intake-design.md`](../specs/2026-10-17-referral-intake-design.md). Context: [`docs/context/challenge-brief.md`](../../context/challenge-brief.md).

## Global Constraints

- Model ID is exactly `claude-opus-5-5` (env `MODEL` may override). Use adaptive thinking (`thinking={"type": "adaptive"}`) and set `output_config={"effort": ...}` explicitly. Never send `budget_tokens`, `temperature`, or an assistant prefill.
- **Never commit** `*.pdf`, `*.zip`, `data/`, `.env`. Packets live in `../claude-impact-lab-oc/challenges/1-referral-intake/packets/` (outside this repo).
- Nothing in `app/` may read `eval/decisions.json`. Only `eval/run.py` reads it, from a CLI argument, **after** all packets are processed.
- No invented facts. A fact the packet doesn't state is `unknown`/`None`. `[REDACTED]` never means missing or negative.
- Kipu is mocked (`MockKipuClient`). No code sends email, fax, SMS, or calls any IHH system.
- Secrets only via env (`ANTHROPIC_API_KEY`). `.env.example` ships with it empty.
- All timestamps are naive UTC (`datetime.now(timezone.utc).replace(tzinfo=None)`) for SQLite.
- Tests never touch the network. Live tests are marked `@pytest.mark.live` and skipped by default.
- Test fixtures are **invented** text only. Never paste packet content into tests, docs or commits.
- Shared types come from `app/schemas.py`. Don't redefine them. If a change is needed, note it in your report for the integration step.
- Each workstream documents its module in `docs/modules/<name>.md` (purpose, public functions, how to test, known limits).

## Review Focus

- **Coordinator double-submits a decision** (double click on Approve): the second call must return 409 and must not create a second assignment. Pinned in Task 13.
- **Hold expired and another referral took the bed** before approval: approve returns 409 with a clear message, and the referral stays `ready_for_review`. Pinned in Task 13.
- **Server restarted mid-pipeline**: referrals stuck in `transcribing`/`analyzing` must become `failed` with "interrupted, retry", so they aren't stuck forever. Pinned in Task 13 (`mark_interrupted`) and wired in Task 14.
- **Model cites a page that doesn't exist** (page 99 of 20) or an off-by-one page: the verifier must not crash. It corrects ±1 or marks the evidence unverified. Pinned in Task 6.
- **Non-PDF or corrupt upload** (a .docx renamed .pdf, or an empty file): 400 with a message, no orphan referral row. Pinned in Task 14.

---

## Execution map (who does what, in what order)

| Phase | Tasks | Run as |
|---|---|---|
| A. Foundation (lead, ~30 min) | 0, 1, 2 | sequential, lead |
| B. Parallel build (~2 h) | WS1: 3, 4 · WS2: 5, 6, 7, 8 · WS3: 9, 10, 11 · WS4: 12, 13, 14 | 4 agents in parallel, each in its own git worktree, merged back in order WS3 → WS1 → WS2 → WS4 |
| C. Integration + eval (~1 h) | 15, 16 | lead + 1 agent |
| D. Ship (~45 min) | 17 | lead |

WS4 depends on interfaces from WS1–3 but tests against fakes, so it can run in parallel. Its module imports resolve once merged.

---

### Task 0: Scaffold, config, shared schemas, test factories, CLAUDE.md

**Files:**
- Create: `requirements.txt`, `pytest.ini`, `.env.example`, `LICENSE`, `CLAUDE.md`
- Create: `app/__init__.py`, `app/config.py`, `app/schemas.py`, `app/db.py`
- Create: `app/ingest/__init__.py`, `app/rules/__init__.py`, `app/beds/__init__.py`, `app/workflow/__init__.py`, `app/api/__init__.py`, `eval/__init__.py`
- Create: `criteria/admission-criteria.json`, `criteria/admission-criteria.md`, `criteria/referral-form-and-checklist.md` (copied from upstream)
- Create: `tests/__init__.py`, `tests/factories.py`, `tests/test_schemas.py`

**Interfaces:**
- Produces: `app.config.get_settings() -> Settings`; every model in `app.schemas`; `app.db.make_engine(url=None)`, `get_engine()`, `set_engine(engine)`, `init_db(engine=None)`, `get_session()` (FastAPI dependency); `tests.factories.*` builders.

- [ ] **Step 1: Get upstream criteria and put packets next to it (outside this repo)**

```bash
cd "C:/Users/devin/UCI/Claude Hackathon"
git clone --depth 1 https://github.com/travcjohnson/claude-impact-lab-oc.git claude-impact-lab-oc
mkdir -p claude-impact-lab-oc/challenges/1-referral-intake/packets
unzip -o -j referral-intake-hackathon/drive-download-20261017T173056Z-1-001.zip -d claude-impact-lab-oc/challenges/1-referral-intake/packets
mv referral-intake-hackathon/drive-download-20261017T173056Z-1-001.zip .
ls claude-impact-lab-oc/challenges/1-referral-intake/packets   # R01.pdf … R11.pdf
cd referral-intake-hackathon
mkdir -p criteria
cp ../claude-impact-lab-oc/challenges/1-referral-intake/criteria/admission-criteria.json criteria/
cp ../claude-impact-lab-oc/challenges/1-referral-intake/criteria/admission-criteria.md criteria/
cp ../claude-impact-lab-oc/challenges/1-referral-intake/criteria/referral-form-and-checklist.md criteria/
```

- [ ] **Step 2: Write `requirements.txt`, `pytest.ini`, `.env.example`, `LICENSE`**

`requirements.txt`:
```
fastapi>=0.115
uvicorn[standard]>=0.30
python-multipart>=0.0.9
sqlmodel>=0.0.22
pydantic>=2.9
anthropic>=1.15
pymupdf>=1.26
rapidfuzz>=3.9
openpyxl>=3.1
python-dotenv>=1.0
pytest>=8
pytest-asyncio>=0.24
httpx>=0.27
```

`pytest.ini`:
```ini
[pytest]
pythonpath = .
testpaths = tests
asyncio_mode = auto
markers =
    live: calls the real Claude API (needs ANTHROPIC_API_KEY); run with -m live
addopts = -m "not live"
```

`.env.example`:
```
# Copy to .env and fill in. Never commit .env.
ANTHROPIC_API_KEY=
MODEL=claude-opus-5-5
DATA_DIR=./data
PACKETS_DIR=../claude-impact-lab-oc/challenges/1-referral-intake/packets
HOLD_TTL_HOURS=4
OCR_CONCURRENCY=8
RULES_CONCURRENCY=8
```

`LICENSE`: the standard MIT license text, with `Copyright (c) 2026 Referral Intake Hackathon Team`.

Then run:
```bash
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt
```

- [ ] **Step 3: Write `app/config.py`**

```python
"""Runtime settings, read from the environment (and .env) on every call so tests can override them."""
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    anthropic_api_key: str
    model: str
    data_dir: Path
    packets_dir: Path | None
    hold_ttl_hours: float
    ocr_concurrency: int
    rules_concurrency: int
    criteria_path: Path

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def outbox_dir(self) -> Path:
        return self.data_dir / "outbox"


def get_settings() -> Settings:
    packets = os.getenv("PACKETS_DIR")
    return Settings(
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
        model=os.getenv("MODEL", "claude-opus-5-5"),
        data_dir=Path(os.getenv("DATA_DIR", str(ROOT / "data"))),
        packets_dir=Path(packets) if packets else None,
        hold_ttl_hours=float(os.getenv("HOLD_TTL_HOURS", "4")),
        ocr_concurrency=int(os.getenv("OCR_CONCURRENCY", "8")),
        rules_concurrency=int(os.getenv("RULES_CONCURRENCY", "8")),
        criteria_path=ROOT / "criteria" / "admission-criteria.json",
    )
```

- [ ] **Step 4: Write `app/schemas.py`** (the contract every workstream shares)

```python
"""Shared models: the contract between ingest, rules, beds, workflow and the API.

See docs/superpowers/specs/2026-10-17-referral-intake-design.md section 5. Models that Claude fills in
carry Field descriptions because they become the structured-output JSON schema.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

RuleKind = Literal["must", "exclude", "document", "conditional", "ask", "note", "eligible"]
RuleSource = Literal["outline", "by_system", "form", "checklist"]
RuleStatus = Literal["pass", "fail", "unknown", "not_applicable", "needs_human"]
Confidence = Literal["high", "medium", "low"]
Sex = Literal["male", "female", "unknown"]
Orientation = Literal["upright", "rotated_90", "rotated_180", "rotated_270", "unknown"]
Legibility = Literal["good", "partial", "poor"]
Decision = Literal["approve", "deny", "needs_review"]
ReferralStatus = Literal[
    "uploaded", "transcribing", "analyzing", "ready_for_review",
    "approved", "denied", "info_requested", "failed",
]
BedStatus = Literal["available", "held", "occupied", "out_of_service"]
SexDesignation = Literal["male", "female", "any"]


# ---------- Criteria ----------
class Rule(BaseModel):
    id: str
    source: RuleSource
    section: str
    kind: RuleKind
    text: str
    group: str
    human_review: bool = False
    superseded_by: str | None = None


# ---------- Ingest ----------
class PageMarkdown(BaseModel):
    page: int = Field(description="1-indexed page number in the PDF")
    doc_type: str = Field(description="Kind of document on this page, e.g. 'H&P', 'PT evaluation', 'IHH referral form'")
    orientation: Orientation = Field(description="How the page content is turned in the image you were shown")
    legibility: Legibility
    markdown: str = Field(description="Faithful transcription. [REDACTED] for black boxes, [illegible] for unreadable text")


class PacketText(BaseModel):
    packet_hash: str
    page_count: int
    pages: list[PageMarkdown]

    def to_markdown(self) -> str:
        return "\n\n".join(f"## Page {p.page} — {p.doc_type}\n\n{p.markdown}" for p in self.pages)


# ---------- Rule evaluation ----------
class Evidence(BaseModel):
    page: int = Field(description="Page number exactly as given in the packet headings")
    quote: str = Field(description="Short verbatim excerpt copied exactly from that page's transcription, max 300 characters")
    verified: bool = Field(default=False, description="Always false; set by the verifier")


class RuleResult(BaseModel):
    rule_id: str
    status: RuleStatus
    reasoning: str = Field(description="1-3 plain sentences a non-clinician can follow")
    evidence: list[Evidence] = []
    answer: str | None = Field(default=None, description="Only for kind=ask: the answer found in the packet, or 'not stated'")
    hospital_question: str | None = Field(default=None, description="When status is unknown: one specific question to ask the hospital")
    confidence: Confidence


class RuleGroupOutput(BaseModel):
    results: list[RuleResult]


class Conflict(BaseModel):
    topic: str
    statements: list[Evidence] = Field(description="At least two statements from the packet that disagree")
    related_rule_ids: list[str]
    why_it_matters: str


class BedNeeds(BaseModel):
    sex: Sex
    wheelchair: bool | None = Field(default=None, description="True only if wheelchair dependent")
    mobility_aid: str | None = None
    oxygen: bool | None = None
    isolation: bool | None = None
    notes: str | None = None


class DocumentPresence(BaseModel):
    doc_id: str = Field(description="Checklist ID, DOC-1 to DOC-10")
    pages: list[int]


class CaseSummary(BaseModel):
    one_liner: str = Field(description="One sentence, no names or identifiers")
    age: str | None = None
    sex: Sex
    admitting_diagnosis: str | None = None
    housing_situation: str | None = None
    has_ihh_referral_form: bool
    documents_present: list[DocumentPresence] = []
    key_findings: list[str] = []
    conflicts: list[Conflict] = []
    bed_needs: BedNeeds


# ---------- Decision tree ----------
class ReviewReason(BaseModel):
    kind: Literal["missing_info", "judgment_call", "unverified_evidence", "conflict", "no_bed"]
    rule_ids: list[str]
    detail: str


class HospitalQuestion(BaseModel):
    rule_id: str
    question: str


class TreeStep(BaseModel):
    node: Literal["exclusions", "must_rules", "general_eligibility", "human_review", "missing_info", "unverified", "conflicts", "bed"]
    outcome: Literal["triggered", "clear"]
    rule_ids: list[str]
    explanation: str


class BedOption(BaseModel):
    bed_id: str
    site: str
    room: str
    label: str
    match_notes: list[str] = []


class Recommendation(BaseModel):
    decision: Decision
    headline: str
    top_reasons: list[str]
    review_reasons: list[ReviewReason]
    hospital_questions: list[HospitalQuestion]
    tree: list[TreeStep]
    held_bed: BedOption | None = None
    alternative_beds: list[BedOption] = []


class AnalysisResult(BaseModel):
    prompt_version: str
    model: str
    packet: PacketText
    summary: CaseSummary
    rule_results: list[RuleResult]
    recommendation: Recommendation
    timings: dict[str, float] = {}


# ---------- Coordinator ----------
class RuleOverride(BaseModel):
    rule_id: str
    status: RuleStatus
    note: str


class CoordinatorDecision(BaseModel):
    decision: Literal["approve", "deny", "request_info"]
    coordinator: str
    notes: str = ""
    rule_overrides: list[RuleOverride] = []
    bed_id: str | None = None


class CoordinatorDecisionRecord(CoordinatorDecision):
    decided_at: datetime
    ai_recommendation: Decision
    agreed_with_ai: bool


class Drafts(BaseModel):
    denial: str | None = None
    info_request: str | None = None
    kipu_payload: dict[str, Any] | None = None
    kipu_reference: str | None = None


# ---------- API views ----------
class RuleResultView(RuleResult):
    kind: RuleKind
    section: str
    source: RuleSource
    text: str
    superseded_by: str | None = None
    human_review: bool = False


class PageInfo(BaseModel):
    page: int
    doc_type: str
    orientation: Orientation
    legibility: Legibility


class ReferralListItem(BaseModel):
    id: str
    filename: str
    status: ReferralStatus
    decision: Decision | None = None
    headline: str | None = None
    created_at: datetime


class ReferralView(ReferralListItem):
    progress: str | None = None
    error: str | None = None
    one_liner: str | None = None
    recommendation: Recommendation | None = None
    coordinator_decision: CoordinatorDecisionRecord | None = None


class ReferralDetails(BaseModel):
    id: str
    status: ReferralStatus
    prompt_version: str
    model: str
    summary: CaseSummary
    rule_results: list[RuleResultView]
    recommendation: Recommendation
    pages: list[PageInfo]
    timings: dict[str, float]


class BedView(BaseModel):
    id: str
    site: str
    room: str
    label: str
    sex_designation: SexDesignation
    wheelchair_accessible: bool
    ground_floor: bool
    oxygen_ok: bool
    isolation_ok: bool
    status: BedStatus
    notes: str = ""
    referral_id: str | None = None
    assignment_id: int | None = None
    hold_expires_at: datetime | None = None


class BedCreate(BaseModel):
    site: str
    room: str
    label: str
    id: str | None = None
    sex_designation: SexDesignation = "any"
    wheelchair_accessible: bool = False
    ground_floor: bool = False
    oxygen_ok: bool = False
    isolation_ok: bool = False
    notes: str = ""


class BedUpdate(BaseModel):
    sex_designation: SexDesignation | None = None
    wheelchair_accessible: bool | None = None
    ground_floor: bool | None = None
    oxygen_ok: bool | None = None
    isolation_ok: bool | None = None
    status: Literal["available", "out_of_service"] | None = None
    notes: str | None = None


class SiteCount(BaseModel):
    site: str
    total: int
    available: int


class BedSummary(BaseModel):
    total: int
    available: int
    held: int
    occupied: int
    out_of_service: int
    by_site: list[SiteCount]


class AssignmentView(BaseModel):
    id: int
    bed_id: str
    referral_id: str
    client_label: str
    start_date: date
    expected_end_date: date | None = None
    released_at: datetime | None = None
    status: Literal["active", "released"]


class BedImportReport(BaseModel):
    created: int
    updated: int
    errors: list[str]


class AuditEventView(BaseModel):
    id: int
    ts: datetime
    actor: str
    referral_id: str | None
    action: str
    detail: dict[str, Any]
```

- [ ] **Step 5: Write `app/db.py`**

```python
"""SQLite engine and session helpers. Table models live in app/beds/models.py and app/workflow/models.py."""
from collections.abc import Iterator

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.config import get_settings

_engine = None


def make_engine(url: str | None = None):
    if url is None:
        settings = get_settings()
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        url = f"sqlite:///{settings.data_dir / 'app.db'}"
    kwargs: dict = {"connect_args": {"check_same_thread": False}}
    if url == "sqlite://":
        kwargs["poolclass"] = StaticPool  # one shared in-memory DB for tests
    return create_engine(url, **kwargs)


def get_engine():
    global _engine
    if _engine is None:
        _engine = make_engine()
    return _engine


def set_engine(engine) -> None:
    global _engine
    _engine = engine


def init_db(engine=None) -> None:
    import app.beds.models  # noqa: F401  (registers tables)
    import app.workflow.models  # noqa: F401

    SQLModel.metadata.create_all(engine or get_engine())


def get_session() -> Iterator[Session]:
    with Session(get_engine()) as session:
        yield session
```

- [ ] **Step 6: Write `tests/factories.py`** (invented data, shared by all test suites)

```python
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
        bed_needs=BedNeeds(sex="male", wheelchair=False, mobility_aid="walker", oxygen=False, isolation=False),
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
```

- [ ] **Step 7: Write the failing test `tests/test_schemas.py`**

```python
from app.schemas import CoordinatorDecision, RuleGroupOutput
from tests.factories import make_analysis, make_packet


def test_packet_to_markdown_has_page_headings():
    md = make_packet().to_markdown()
    assert md.startswith("## Page 1 — other")
    assert "## Page 3 — other" in md
    assert "ambulated 150 ft" in md


def test_analysis_round_trips_json():
    a = make_analysis()
    assert type(a).model_validate_json(a.model_dump_json()) == a


def test_rule_group_output_schema_is_strict_compatible():
    schema = RuleGroupOutput.model_json_schema()
    assert "results" in schema["properties"]


def test_coordinator_decision_defaults():
    d = CoordinatorDecision(decision="approve", coordinator="Test Coordinator")
    assert d.rule_overrides == [] and d.bed_id is None
```

- [ ] **Step 8: Run tests**

Run: `.venv/Scripts/python -m pytest tests/test_schemas.py -v`
Expected: 4 passed. If the factories or schemas don't import, fix them until all pass.

- [ ] **Step 9: Write `CLAUDE.md`** (guidance for every future agent in this repo)

```markdown
# CLAUDE.md

Hackathon prototype (Claude Impact Lab OC, 2026-10-17) for Illumination Health + Home (IHH): AI-assisted referral
intake + bed management. Submissions due 4:00 PM.

Read first: `docs/context/challenge-brief.md`, then `docs/superpowers/specs/2026-10-17-referral-intake-design.md`,
then `docs/superpowers/plans/2026-10-17-referral-intake.md`. Module notes are in `docs/modules/`.

## Rules that do not bend
- Never copy packet PDFs, the ZIP, OCR output or quotes from packets into this repo or its history. Packets live in
  `../claude-impact-lab-oc/challenges/1-referral-intake/packets/`. Generated data lives in `data/` (gitignored).
- If a packet page shows an unredacted identifier: stop and tell the user to find an organizer.
- Nothing connects to IHH systems (Kipu is mocked) or sends real messages. Drafts only.
- Never invent facts. Unknown is "unknown". `[REDACTED]` is not "missing".
- `app/` never reads `eval/decisions.json`.
- Secrets only in `.env` (gitignored).

## Working here
- Python venv: `.venv`. Run tests: `.venv/Scripts/python -m pytest -q`. Run API: `.venv/Scripts/uvicorn app.main:app --reload`.
- Shared types: `app/schemas.py`. Rule IDs come from `criteria/admission-criteria.json`.
- Model: `claude-opus-5-5` via `app/llm.py` only (adaptive thinking, explicit effort, refusal handling).
- When criteria are unclear or sources disagree, surface it to the coordinator. Don't pick silently.
- Commit often; never commit `data/`, PDFs, or `.env`.
```

- [ ] **Step 10: Commit**

```bash
git add requirements.txt pytest.ini .env.example LICENSE CLAUDE.md app criteria eval tests
git status --short   # confirm: no .pdf, .zip, data/, .env
git commit -m "chore: scaffold app, shared schemas, config, db, criteria copy, CLAUDE.md"
```

---

### Task 1: `app/llm.py`: structured Claude calls with refusal handling

**Files:**
- Create: `app/llm.py`, `tests/fakes.py`, `tests/test_llm.py`

**Interfaces:**
- Consumes: `get_settings()`.
- Produces: `class LLMError(RuntimeError)`; `class StructuredLLM(client=None, model=None, use_fallbacks=True)` with `model: str` and `async parse(*, system: str, content: list[dict], output: type[T], effort: str = "high", max_tokens: int = 16000) -> T`. `tests.fakes.FakeLLM(handler)` has the same `parse` signature; `handler(output_type, system, content)` returns an instance or an Exception.

- [ ] **Step 1: Write `tests/fakes.py`**

```python
"""Test doubles for StructuredLLM."""
from types import SimpleNamespace


class FakeLLM:
    """Same interface as app.llm.StructuredLLM. handler(output_type, system, content) -> instance or Exception."""

    model = "fake-model"

    def __init__(self, handler):
        self.handler = handler
        self.calls: list[dict] = []

    async def parse(self, *, system, content, output, effort="high", max_tokens=16000):
        self.calls.append({"system": system, "content": content, "output": output, "effort": effort})
        result = self.handler(output, system, content)
        if isinstance(result, Exception):
            raise result
        return result


class FakeMessages:
    """Stands in for client.beta.messages: returns queued responses or raises queued exceptions."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[dict] = []

    async def parse(self, **kwargs):
        self.calls.append(kwargs)
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def fake_client(responses) -> SimpleNamespace:
    return SimpleNamespace(beta=SimpleNamespace(messages=FakeMessages(responses)))


def fake_response(parsed, stop_reason="end_turn", category=None) -> SimpleNamespace:
    return SimpleNamespace(parsed_output=parsed, stop_reason=stop_reason,
                           stop_details=SimpleNamespace(category=category) if category else None)
```

- [ ] **Step 2: Write the failing tests `tests/test_llm.py`**

```python
import pytest
from pydantic import BaseModel, ValidationError

from app.llm import FALLBACK_BETA, LLMError, StructuredLLM
from tests.fakes import fake_client, fake_response


class Out(BaseModel):
    answer: str


def _validation_error() -> ValidationError:
    try:
        Out.model_validate({})
    except ValidationError as e:
        return e
    raise AssertionError


async def test_returns_parsed_output_and_sends_required_params():
    client = fake_client([fake_response(Out(answer="ok"))])
    llm = StructuredLLM(client=client, model="claude-opus-5-5")
    out = await llm.parse(system="sys", content=[{"type": "text", "text": "hi"}], output=Out, effort="low")
    assert out.answer == "ok"
    call = client.beta.messages.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["thinking"] == {"type": "adaptive"}
    assert call["output_config"] == {"effort": "low"}
    assert call["output_format"] is Out
    assert call["betas"] == [FALLBACK_BETA] and call["fallbacks"] == "default"
    assert call["messages"] == [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]


async def test_refusal_raises():
    llm = StructuredLLM(client=fake_client([fake_response(None, stop_reason="refusal", category="bio")]))
    with pytest.raises(LLMError, match="declined"):
        await llm.parse(system="s", content=[], output=Out)


async def test_truncation_raises():
    llm = StructuredLLM(client=fake_client([fake_response(None, stop_reason="max_tokens")]))
    with pytest.raises(LLMError, match="truncated"):
        await llm.parse(system="s", content=[], output=Out)


async def test_retries_once_on_validation_error():
    client = fake_client([_validation_error(), fake_response(Out(answer="second"))])
    out = await StructuredLLM(client=client).parse(system="s", content=[], output=Out)
    assert out.answer == "second" and len(client.beta.messages.calls) == 2


async def test_gives_up_after_two_bad_outputs():
    client = fake_client([fake_response(None), fake_response(None)])
    with pytest.raises(LLMError, match="no structured output"):
        await StructuredLLM(client=client).parse(system="s", content=[], output=Out)


async def test_without_fallbacks_omits_beta_params():
    client = fake_client([fake_response(Out(answer="ok"))])
    await StructuredLLM(client=client, use_fallbacks=False).parse(system="s", content=[], output=Out)
    call = client.beta.messages.calls[0]
    assert "fallbacks" not in call and "betas" not in call
```

- [ ] **Step 3: Run to verify failure**

Run: `.venv/Scripts/python -m pytest tests/test_llm.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.llm'`

- [ ] **Step 4: Write `app/llm.py`**

```python
"""The only place that talks to Claude. Structured output, adaptive thinking, refusal handling."""
import logging
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from app.config import get_settings

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class LLMError(RuntimeError):
    """A Claude call that could not produce a usable structured answer."""


class StructuredLLM:
    def __init__(self, client: Any | None = None, model: str | None = None, use_fallbacks: bool = True):
        self._client = client
        self.model = model or get_settings().model
        self.use_fallbacks = use_fallbacks

    @property
    def client(self) -> Any:
        if self._client is None:
            import anthropic

            self._client = anthropic.AsyncAnthropic(max_retries=3)
        return self._client

    async def parse(self, *, system: str, content: list[dict], output: type[T],
                    effort: str = "high", max_tokens: int = 16000) -> T:
        last_error: Exception | None = None
        for _attempt in range(2):
            kwargs: dict[str, Any] = dict(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": content}],
                output_format=output,
                output_config={"effort": effort},
                thinking={"type": "adaptive"},
            )
            if self.use_fallbacks:
                kwargs["betas"] = [FALLBACK_BETA]
                kwargs["fallbacks"] = "default"
            try:
                response = await self.client.beta.messages.parse(**kwargs)
            except ValidationError as e:  # model JSON did not match the schema
                last_error = e
                continue
            except Exception as e:  # server rejected fallbacks on this account: retry without them
                if self.use_fallbacks and "fallback" in str(e).lower():
                    log.warning("Server-side fallbacks rejected; continuing without them: %s", e)
                    self.use_fallbacks = False
                    continue
                raise
            if response.stop_reason == "refusal":
                category = getattr(getattr(response, "stop_details", None), "category", None)
                raise LLMError(f"Model declined the request (category={category})")
            if response.stop_reason == "max_tokens":
                raise LLMError("Model output was truncated (max_tokens)")
            if response.parsed_output is not None:
                return response.parsed_output
            last_error = LLMError("Model returned no structured output")
        raise LLMError(f"Structured output failed after retry: no structured output ({last_error})")
```

- [ ] **Step 5: Run tests**

Run: `.venv/Scripts/python -m pytest tests/test_llm.py -v`
Expected: 6 passed

- [ ] **Step 6: Commit**

```bash
git add app/llm.py tests/fakes.py tests/test_llm.py
git commit -m "feat: structured Claude client with refusal/truncation handling and fakes"
```

---

### Task 2: API contract for the frontend teammate

**Files:**
- Create: `docs/api-contract.md`

**Interfaces:**
- Consumes: the view models in `app/schemas.py` (Task 0). Produces: the document the frontend codes against.

- [ ] **Step 1: Write `docs/api-contract.md`**

````markdown
# API contract (frontend ↔ backend)

Base URL (local): `http://localhost:8000`. Interactive docs once running: `http://localhost:8000/docs`.
Types are defined in [`app/schemas.py`](../app/schemas.py). Field names below match it exactly.
CORS is open. All bodies are JSON unless noted. Times are UTC ISO-8601 without a timezone suffix.

## Coordinator flow
1. `POST /api/referrals` (multipart, field `file` = PDF) → `202 {"id": "R-7F3A2C", "status": "uploaded", ...}`
2. Poll `GET /api/referrals/{id}` every 2 s while `status` is `uploaded | transcribing | analyzing`. `progress` holds
   human-readable text, e.g. "Transcribing page 12 of 34".
3. When `status == "ready_for_review"`, render the **summary card** from `recommendation`:
   `decision` (`approve | deny | needs_review`), `headline`, `top_reasons[]` (≤3 strings ending in `[RULE-ID]`),
   `held_bed` (`{bed_id, site, room, label, match_notes[]}` or null), `review_reasons[]`, `hospital_questions[]`.
4. "Show details" → `GET /api/referrals/{id}/details`: `rule_results[]` (each has `rule_id, kind, section, text,
   status, reasoning, evidence[{page, quote, verified}], hospital_question, confidence`), `recommendation.tree[]`
   (the decision path: `node, outcome, rule_ids, explanation`), `summary.conflicts[]`, `pages[]`.
   Evidence → `GET /api/referrals/{id}/pages/{n}/image` (PNG) shows the scan, and `GET /api/referrals/{id}/pages/{n}` the transcription.
   Show `verified: false` evidence with a warning style.
5. Decide → `POST /api/referrals/{id}/decision` with
   `{"decision": "approve|deny|request_info", "coordinator": "Name", "notes": "", "rule_overrides": [], "bed_id": null}`.
   `bed_id: null` uses the held bed. `409` means the bed is no longer available or it was already decided; show
   `detail` and offer `GET /api/referrals/{id}/bed-matches`.
6. `GET /api/referrals/{id}/drafts` → `{denial, info_request, kipu_payload, kipu_reference}` (drafts are never sent).

## Endpoints
| Method | Path | Body / query | Returns |
|---|---|---|---|
| GET | `/api/health` | | `{ok, model, api_key_configured}` |
| POST | `/api/referrals` | multipart `file` | `202 ReferralView` · `400` not a PDF · `503` no API key |
| GET | `/api/referrals` | | `ReferralListItem[]` (newest first) |
| GET | `/api/referrals/{id}` | | `ReferralView` |
| GET | `/api/referrals/{id}/details` | | `ReferralDetails` · `409` if not analyzed yet |
| GET | `/api/referrals/{id}/pages/{n}` | | `PageMarkdown` |
| GET | `/api/referrals/{id}/pages/{n}/image` | | `image/png` (rotated upright) |
| GET | `/api/referrals/{id}/bed-matches` | | `BedOption[]` |
| POST | `/api/referrals/{id}/decision` | `CoordinatorDecision` | `ReferralView` · `409` |
| GET | `/api/referrals/{id}/drafts` | | `Drafts` |
| POST | `/api/referrals/{id}/retry` | | `202 ReferralView` (only when `failed`) |
| GET | `/api/beds` | `?site=&status=` | `BedView[]` |
| GET | `/api/beds/summary` | | `BedSummary` |
| POST | `/api/beds` | `BedCreate` | `BedView` |
| PATCH | `/api/beds/{bed_id}` | `BedUpdate` | `BedView` |
| POST | `/api/beds/import` | multipart `file` (.csv/.xlsx) | `BedImportReport` |
| GET | `/api/assignments` | `?active=true` | `AssignmentView[]` |
| POST | `/api/assignments/{id}/discharge` | | `AssignmentView` |
| GET | `/api/audit` | `?referral_id=` | `AuditEventView[]` |

## Status vocabularies
- Referral `status`: `uploaded → transcribing → analyzing → ready_for_review → approved | denied | info_requested`, or `failed`.
- Rule `status`: `pass | fail | unknown | not_applicable | needs_human` (always from the eligibility point of view).
- Bed `status`: `available | held | occupied | out_of_service`.

## Example `GET /api/referrals/{id}` (invented data)
```json
{
  "id": "R-7F3A2C", "filename": "packet.pdf", "status": "ready_for_review", "decision": "needs_review",
  "headline": "Needs review: 2 items to resolve before deciding", "created_at": "2026-10-17T18:02:11",
  "progress": null, "error": null,
  "one_liner": "Adult man referred after inpatient stay for left-leg cellulitis; walks with a walker.",
  "recommendation": {
    "decision": "needs_review", "headline": "Needs review: 2 items to resolve before deciding",
    "top_reasons": ["Missing information: MRSA swab result not in packet [INF-1]"],
    "review_reasons": [{"kind": "missing_info", "rule_ids": ["INF-1"], "detail": "MRSA swab result not in packet"}],
    "hospital_questions": [{"rule_id": "INF-1", "question": "Can you send the MRSA nares swab result?"}],
    "tree": [{"node": "exclusions", "outcome": "clear", "rule_ids": [], "explanation": "No exclusion found."}],
    "held_bed": {"bed_id": "SA-101-A", "site": "Site A (demo)", "room": "101", "label": "A", "match_notes": ["male room"]},
    "alternative_beds": []
  },
  "coordinator_decision": null
}
```
````

- [ ] **Step 2: Commit and tell the user to share it with the frontend teammate**

```bash
git add docs/api-contract.md
git commit -m "docs: API contract for frontend"
```

---

### Task 3 (WS1): PDF rendering

**Files:**
- Create: `app/ingest/render.py`, `tests/test_render.py`

**Interfaces:**
- Produces: `class InvalidPDF(ValueError)`; `file_sha256(path: Path) -> str`; `validate_pdf(path: Path) -> int` (page count, raises `InvalidPDF`); `page_count(path: Path) -> int`; `render_page(pdf_path: Path, page: int, rotate: int = 0, dpi: int = 150) -> tuple[bytes, str]` (page is 1-indexed; `rotate` = degrees **clockwise**; returns `(bytes, media_type)`); `cached_page_image(pdf_path, packet_hash, page, cache_root, rotate=0) -> tuple[bytes, str]`.

- [ ] **Step 1: Write the failing tests `tests/test_render.py`**

```python
from pathlib import Path

import pymupdf
import pytest

from app.ingest.render import InvalidPDF, cached_page_image, file_sha256, page_count, render_page, validate_pdf


@pytest.fixture
def pdf(tmp_path: Path) -> Path:
    doc = pymupdf.open()
    for i in range(2):
        p = doc.new_page(width=200, height=200)
        p.draw_rect(pymupdf.Rect(0, 0, 100, 100), color=(0, 0, 0), fill=(0, 0, 0))  # black top-left quadrant
        p.insert_text((110, 150), f"page {i + 1}")
    path = tmp_path / "t.pdf"
    doc.save(path)
    return path


def test_page_count_and_validate(pdf):
    assert page_count(pdf) == 2
    assert validate_pdf(pdf) == 2


def test_validate_rejects_non_pdf(tmp_path):
    bad = tmp_path / "x.pdf"
    bad.write_bytes(b"PK\x03\x04 this is a docx")
    with pytest.raises(InvalidPDF):
        validate_pdf(bad)


def test_validate_rejects_empty(tmp_path):
    bad = tmp_path / "empty.pdf"
    bad.write_bytes(b"")
    with pytest.raises(InvalidPDF):
        validate_pdf(bad)


def test_render_png(pdf):
    data, media = render_page(pdf, 1, dpi=72)
    assert media == "image/png" and data.startswith(b"\x89PNG")


def test_rotate_is_clockwise(pdf):
    data, _ = render_page(pdf, 1, rotate=90, dpi=72)
    pix = pymupdf.Pixmap(data)
    w = pix.width
    assert pix.pixel(w - 10, 10)[0] < 50  # black quadrant moved top-left -> top-right
    assert pix.pixel(10, 10)[0] > 200


def test_cache_writes_once(pdf, tmp_path):
    h = file_sha256(pdf)
    a = cached_page_image(pdf, h, 2, tmp_path / "cache")
    files = list((tmp_path / "cache" / h / "pages").iterdir())
    assert len(files) == 1
    b = cached_page_image(pdf, h, 2, tmp_path / "cache")
    assert a == b
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python -m pytest tests/test_render.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.ingest.render'`

- [ ] **Step 3: Write `app/ingest/render.py`**

```python
"""PDF page rendering with PyMuPDF. Faxes are black-and-white, so pages render in grayscale."""
import hashlib
from pathlib import Path

import pymupdf

MAX_PNG_BYTES = 4_000_000


class InvalidPDF(ValueError):
    """The upload is not a readable, unencrypted PDF with at least one page."""


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def validate_pdf(path: Path) -> int:
    try:
        with pymupdf.open(path, filetype="pdf") as doc:
            if doc.needs_pass:
                raise InvalidPDF("PDF is password protected")
            if doc.page_count < 1:
                raise InvalidPDF("PDF has no pages")
            return doc.page_count
    except InvalidPDF:
        raise
    except Exception as e:
        raise InvalidPDF(f"Not a readable PDF: {e}") from e


def page_count(path: Path) -> int:
    with pymupdf.open(path, filetype="pdf") as doc:
        return doc.page_count


def render_page(pdf_path: Path, page: int, rotate: int = 0, dpi: int = 150) -> tuple[bytes, str]:
    """Render 1-indexed `page`, rotated `rotate` degrees clockwise. Returns (bytes, media_type)."""
    with pymupdf.open(pdf_path, filetype="pdf") as doc:
        zoom = dpi / 72
        matrix = pymupdf.Matrix(zoom, zoom).prerotate(rotate)
        pix = doc[page - 1].get_pixmap(matrix=matrix, colorspace=pymupdf.csGRAY)
        png = pix.tobytes("png")
        if len(png) <= MAX_PNG_BYTES:
            return png, "image/png"
        return pix.tobytes("jpeg", jpg_quality=85), "image/jpeg"


def cached_page_image(pdf_path: Path, packet_hash: str, page: int, cache_root: Path,
                      rotate: int = 0) -> tuple[bytes, str]:
    folder = cache_root / packet_hash / "pages"
    folder.mkdir(parents=True, exist_ok=True)
    for ext, media in (("png", "image/png"), ("jpg", "image/jpeg")):
        f = folder / f"{page}_r{rotate}.{ext}"
        if f.exists():
            return f.read_bytes(), media
    data, media = render_page(pdf_path, page, rotate)
    ext = "png" if media == "image/png" else "jpg"
    (folder / f"{page}_r{rotate}.{ext}").write_bytes(data)
    return data, media
```

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python -m pytest tests/test_render.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add app/ingest/render.py tests/test_render.py
git commit -m "feat(ingest): PDF validation, grayscale page rendering, rotation, image cache"
```

---

### Task 4 (WS1): Page transcription (OCR) with cache and orientation retry

**Files:**
- Create: `app/ingest/ocr.py`, `tests/test_ocr.py`, `docs/modules/ingest.md`

**Interfaces:**
- Consumes: `StructuredLLM.parse` (or `FakeLLM`), `render.cached_page_image`, `render.file_sha256`, `render.page_count`, `PageMarkdown`, `PacketText`.
- Produces: `CORRECTION: dict[str, int]` (orientation → clockwise degrees that make it upright); `FAILED_MARKER = "[transcription failed]"`; `async transcribe_packet(llm, pdf_path: Path, cache_root: Path, concurrency: int = 8, on_progress: Callable[[int, int], None] | None = None) -> PacketText`.

- [ ] **Step 1: Write the failing tests `tests/test_ocr.py`**

```python
from pathlib import Path

import pymupdf
import pytest

from app.ingest.ocr import CORRECTION, FAILED_MARKER, transcribe_packet
from app.llm import LLMError
from app.schemas import PageMarkdown
from tests.fakes import FakeLLM


@pytest.fixture
def pdf(tmp_path: Path) -> Path:
    doc = pymupdf.open()
    for i in range(3):
        doc.new_page(width=200, height=200).insert_text((20, 50), f"invented page {i + 1}")
    path = tmp_path / "p.pdf"
    doc.save(path)
    return path


def upright(output, system, content):
    return PageMarkdown(page=0, doc_type="other", orientation="upright", legibility="good", markdown="text")


async def test_transcribes_all_pages_in_order_with_page_numbers(pdf, tmp_path):
    llm = FakeLLM(upright)
    progress = []
    packet = await transcribe_packet(llm, pdf, tmp_path / "c", concurrency=2,
                                     on_progress=lambda d, n: progress.append((d, n)))
    assert [p.page for p in packet.pages] == [1, 2, 3]
    assert packet.page_count == 3 and len(llm.calls) == 3
    assert progress[-1] == (3, 3)
    first = llm.calls[0]["content"]
    assert first[0]["type"] == "image" and first[0]["source"]["media_type"] == "image/png"
    assert llm.calls[0]["effort"] == "low"


async def test_second_run_uses_cache(pdf, tmp_path):
    await transcribe_packet(FakeLLM(upright), pdf, tmp_path / "c")
    llm2 = FakeLLM(upright)
    packet = await transcribe_packet(llm2, pdf, tmp_path / "c")
    assert llm2.calls == [] and packet.page_count == 3


async def test_rotated_page_is_retried_upright(pdf, tmp_path):
    state = {"n": 0}

    def handler(output, system, content):
        state["n"] += 1
        if state["n"] == 1:
            return PageMarkdown(page=0, doc_type="labs", orientation="rotated_90", legibility="poor", markdown="garbled")
        return PageMarkdown(page=0, doc_type="labs", orientation="upright", legibility="good", markdown="clean")

    packet = await transcribe_packet(FakeLLM(handler), pdf, tmp_path / "c", concurrency=1)
    p1 = packet.pages[0]
    assert p1.markdown == "clean" and p1.orientation == "rotated_90"  # original orientation kept for the image endpoint
    assert CORRECTION["rotated_90"] == 270


async def test_failed_page_does_not_stop_run_and_is_not_cached(pdf, tmp_path):
    def handler(output, system, content):
        if "page 2" in content[-1]["text"]:
            return LLMError("boom")
        return upright(output, system, content)

    packet = await transcribe_packet(FakeLLM(handler), pdf, tmp_path / "c")
    assert packet.pages[1].markdown == FAILED_MARKER and packet.pages[1].legibility == "poor"
    assert not (tmp_path / "c" / packet.packet_hash / "packet.json").exists()
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python -m pytest tests/test_ocr.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.ingest.ocr'`

- [ ] **Step 3: Write `app/ingest/ocr.py`**

```python
"""Page-by-page transcription of image-only faxes with Claude vision."""
import asyncio
import base64
import logging
from collections.abc import Callable
from pathlib import Path

from app.ingest.render import cached_page_image, file_sha256, page_count
from app.schemas import PacketText, PageMarkdown

log = logging.getLogger(__name__)

FAILED_MARKER = "[transcription failed]"
# Orientation as reported -> clockwise degrees that make the content upright.
CORRECTION = {"rotated_90": 270, "rotated_180": 180, "rotated_270": 90}
LEGIBILITY_RANK = {"good": 2, "partial": 1, "poor": 0}

OCR_SYSTEM = """You transcribe one page of a faxed hospital referral packet for a recuperative-care intake team.
- Transcribe exactly what is on the page, in reading order, as markdown. Keep tables as markdown tables and checkboxes as [x] / [ ].
- Black boxes are privacy redactions: write [REDACTED] in their place. Never guess what was under them.
- Write [illegible] for anything you cannot read. Never complete, infer or correct clinical content.
- Mark handwritten text with "(handwritten)" after it.
- orientation describes the page as shown to you: upright; rotated_90 (content turned 90 degrees clockwise, text runs top to bottom); rotated_180 (upside down); rotated_270 (content turned 90 degrees counter-clockwise).
- doc_type: one of fax cover, IHH referral form, health plan authorization, face sheet, H&P, progress note, nursing note, PT evaluation, OT evaluation, social work note, psychiatry consult, labs, vitals, imaging, ECG, medication record, physician orders, wound note, near-blank, other.
- legibility: good, partial or poor."""


async def _ocr(llm, image: bytes, media_type: str, page: int) -> PageMarkdown:
    content = [
        {"type": "image", "source": {"type": "base64", "media_type": media_type,
                                     "data": base64.standard_b64encode(image).decode("ascii")}},
        {"type": "text", "text": f"This is page {page} of the packet. Transcribe it."},
    ]
    return await llm.parse(system=OCR_SYSTEM, content=content, output=PageMarkdown, effort="low", max_tokens=8000)


async def _transcribe_page(llm, pdf_path: Path, packet_hash: str, page: int, cache_root: Path) -> PageMarkdown:
    for attempt in range(2):
        try:
            image, media = await asyncio.to_thread(cached_page_image, pdf_path, packet_hash, page, cache_root)
            result = await _ocr(llm, image, media, page)
            fix = CORRECTION.get(result.orientation)
            if fix:
                image2, media2 = await asyncio.to_thread(cached_page_image, pdf_path, packet_hash, page, cache_root, fix)
                retry = await _ocr(llm, image2, media2, page)
                if LEGIBILITY_RANK[retry.legibility] >= LEGIBILITY_RANK[result.legibility]:
                    result = retry.model_copy(update={"orientation": result.orientation})
            return result.model_copy(update={"page": page})
        except Exception as e:
            log.warning("Page %s transcription attempt %s failed: %s", page, attempt + 1, e)
    return PageMarkdown(page=page, doc_type="other", orientation="unknown", legibility="poor", markdown=FAILED_MARKER)


async def transcribe_packet(llm, pdf_path: Path, cache_root: Path, concurrency: int = 8,
                            on_progress: Callable[[int, int], None] | None = None) -> PacketText:
    packet_hash = await asyncio.to_thread(file_sha256, pdf_path)
    cache_file = cache_root / packet_hash / "packet.json"
    if cache_file.exists():
        return PacketText.model_validate_json(cache_file.read_text("utf-8"))

    total = await asyncio.to_thread(page_count, pdf_path)
    sem = asyncio.Semaphore(concurrency)
    done = 0

    async def one(page: int) -> PageMarkdown:
        nonlocal done
        async with sem:
            result = await _transcribe_page(llm, pdf_path, packet_hash, page, cache_root)
        done += 1
        if on_progress:
            on_progress(done, total)
        return result

    pages = await asyncio.gather(*(one(p) for p in range(1, total + 1)))
    packet = PacketText(packet_hash=packet_hash, page_count=total, pages=sorted(pages, key=lambda p: p.page))
    if all(p.markdown != FAILED_MARKER for p in packet.pages):
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(packet.model_dump_json(), "utf-8")
    return packet
```

Note: in `test_failed_page_does_not_stop_run_and_is_not_cached`, the handler returns an `LLMError`, which `FakeLLM` raises. `_transcribe_page` catches it twice, then returns the failed-marker page.

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python -m pytest tests/test_ocr.py tests/test_render.py -v`
Expected: all passed

- [ ] **Step 5: Write `docs/modules/ingest.md`**

```markdown
# Ingest module (`app/ingest/`)

**Purpose:** turn an image-only fax PDF into per-page markdown (`PacketText`) with page numbers, doc types,
orientation and legibility.

**Public API**
- `render.validate_pdf(path) -> int`: raises `InvalidPDF` on non-PDF, empty or encrypted files.
- `render.render_page(pdf, page, rotate=0, dpi=150) -> (bytes, media_type)`: `rotate` is clockwise degrees.
- `render.cached_page_image(pdf, hash, page, cache_root, rotate=0)`: renders once into `data/cache/{hash}/pages/`.
- `ocr.transcribe_packet(llm, pdf, cache_root, concurrency=8, on_progress=None) -> PacketText`.

**Behavior**
- One Claude vision call per page (effort `low`), concurrency 8. A page reported as rotated is re-rendered upright and
  transcribed again; the better legibility wins and `orientation` keeps the original value, so the image endpoint can
  correct it with `CORRECTION`.
- A page that fails twice becomes `[transcription failed]` (legibility `poor`), and the packet is then **not** cached,
  so a retry re-attempts it.
- Cache key: sha256 of the PDF bytes → `data/cache/{hash}/packet.json`. Delete it to force a re-OCR (e.g. after a prompt change).

**Testing:** `pytest tests/test_render.py tests/test_ocr.py` (fake LLM, no network).

**Known limits:** ~1 call per page (34 pages ≈ 1–2 min). Handwriting quality depends on the scan.
```

- [ ] **Step 6: Commit**

```bash
git add app/ingest/ocr.py tests/test_ocr.py docs/modules/ingest.md
git commit -m "feat(ingest): concurrent page transcription with orientation retry and cache"
```

---

### Task 5 (WS2): Criteria loader

**Files:**
- Create: `app/rules/criteria.py`, `tests/test_criteria.py`

**Interfaces:**
- Consumes: `criteria/admission-criteria.json`, `Rule`.
- Produces: `GROUP_NAMES: tuple[str, ...]` (8 groups); `class Criteria` (dataclass: `rules: list[Rule]`, `precedence: str`, `kinds: dict[str, str]`, `checklist: list[Rule]`) with `by_id() -> dict[str, Rule]`, `groups() -> dict[str, list[Rule]]` (excludes `superseded`), `superseded() -> list[Rule]`; `load_criteria(path: Path | None = None) -> Criteria`.

- [ ] **Step 1: Write the failing tests `tests/test_criteria.py`**

```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python -m pytest tests/test_criteria.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write `app/rules/criteria.py`**

```python
"""Flatten IHH's admission-criteria.json into Rule objects grouped for parallel evaluation."""
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.config import get_settings
from app.schemas import Rule

GROUP_NAMES = ("function", "behavior", "infection_docs", "disease",
               "systems_a", "systems_b", "systems_c", "systems_d")

SECTION_GROUP = {
    "ADLs": "function", "Mobility": "function", "Cognition": "function",
    "Mental Health": "behavior", "Other Factors": "behavior",
    "Infection Control": "infection_docs", "Infection": "infection_docs", "End of life care": "infection_docs",
    "Diabetes": "disease", "Cardiac": "disease", "Respiratory Illness": "disease",
}
SYSTEM_GROUP = {
    "Integumentary": "systems_a", "Ophthalmology": "systems_a", "Respiratory": "systems_a",
    "Musculoskeletal": "systems_b", "GI": "systems_b",
    "Endocrine": "systems_c", "GU": "systems_c",
    "Circulatory System": "systems_d", "Neurology": "systems_d",
}
FORM_GROUP = {
    "FORM-M1": "function", "FORM-M2": "function", "FORM-M3": "function", "FORM-M4": "function", "FORM-M5": "function",
    "FORM-X1": "behavior", "FORM-X2": "behavior", "FORM-X5": "behavior", "FORM-X7": "behavior",
    "FORM-X4": "infection_docs",
}


@dataclass(frozen=True)
class Criteria:
    rules: list[Rule]
    precedence: str
    kinds: dict[str, str]

    def by_id(self) -> dict[str, Rule]:
        return {r.id: r for r in self.rules}

    def groups(self) -> dict[str, list[Rule]]:
        out: dict[str, list[Rule]] = {g: [] for g in GROUP_NAMES}
        for r in self.rules:
            if r.group != "superseded":
                out[r.group].append(r)
        return out

    def superseded(self) -> list[Rule]:
        return [r for r in self.rules if r.group == "superseded"]

    def checklist(self) -> list[Rule]:
        return [r for r in self.rules if r.source == "checklist"]


@lru_cache(maxsize=4)
def _load(path_str: str) -> Criteria:
    data = json.loads(Path(path_str).read_text("utf-8"))
    rules: list[Rule] = []
    for item in data["general"]["any_of"]:
        rules.append(Rule(id=item["id"], source="outline", section="General admission criteria (meets one or more)",
                          kind="must", text=item["text"], group="function"))
    for item in data["rules"]:
        rules.append(Rule(id=item["id"], source="outline", section=item["section"], kind=item["kind"],
                          text=item["text"], group=SECTION_GROUP[item["section"]],
                          human_review=item.get("human_review", False)))
    for system in data["by_system"]:
        name = system["system"]
        for item in system.get("eligible", []):
            rules.append(Rule(id=item["id"], source="by_system", section=f"{name} (eligible)",
                              kind=item.get("kind", "eligible"), text=item["text"], group=SYSTEM_GROUP[name]))
        for item in system.get("ineligible", []):
            rules.append(Rule(id=item["id"], source="by_system", section=f"{name} (ineligible)",
                              kind="exclude", text=item["text"], group=SYSTEM_GROUP[name]))
    for item in data["referral_checklist"]["items"]:
        rules.append(Rule(id=item["id"], source="checklist", section="Referral checklist", kind="document",
                          text=item["text"], group="infection_docs"))
    form = data["referral_form_screen"]
    for key, kind, section in (("must_meet_all", "must", "Referral form: must meet all"),
                               ("not_eligible_if", "exclude", "Referral form: not eligible if")):
        for item in form[key]:
            sup = item.get("superseded_by")
            rules.append(Rule(id=item["id"], source="form", section=section, kind=kind, text=item["text"],
                              group="superseded" if sup else FORM_GROUP[item["id"]], superseded_by=sup))
    return Criteria(rules=rules, precedence=data["precedence"], kinds=data["kinds"])


def load_criteria(path: Path | None = None) -> Criteria:
    return _load(str(path or get_settings().criteria_path))
```

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python -m pytest tests/test_criteria.py -v`
Expected: 4 passed. If a count differs, print `Counter(r.group for r in c.rules)` and the source JSON section names, and fix the mapping. **Do not change the expected totals** without checking them against the JSON by hand.

- [ ] **Step 5: Commit**

```bash
git add app/rules/criteria.py tests/test_criteria.py
git commit -m "feat(rules): criteria loader with rule groups and superseded form rules"
```

---

### Task 6 (WS2): Quote verifier

**Files:**
- Create: `app/rules/verify.py`, `tests/test_verify.py`

**Interfaces:**
- Consumes: `RuleResult`, `Evidence`, `PacketText`, `CaseSummary`.
- Produces: `normalize(s: str) -> str`; `quote_on_page(quote: str, page_md: str) -> bool`; `verify_evidence(ev: list[Evidence], packet: PacketText) -> list[Evidence]`; `verify_results(results: list[RuleResult], packet: PacketText) -> list[RuleResult]`; `verify_summary(summary: CaseSummary, packet: PacketText) -> CaseSummary`.

- [ ] **Step 1: Write the failing tests `tests/test_verify.py`**

```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python -m pytest tests/test_verify.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write `app/rules/verify.py`**

```python
"""Deterministic check that every quote the model cites really appears on (or next to) the cited page."""
import re

from rapidfuzz import fuzz

from app.schemas import CaseSummary, Evidence, PacketText, RuleResult

FUZZY_THRESHOLD = 90
MIN_FUZZY_LEN = 12
_DROP = re.compile(r"[^a-z0-9.%\s]")
_SPACE = re.compile(r"\s+")


def normalize(s: str) -> str:
    s = _DROP.sub(" ", s.lower())
    return _SPACE.sub(" ", s).strip()


def quote_on_page(quote: str, page_md: str) -> bool:
    q, text = normalize(quote), normalize(page_md)
    if len(q) < 3:
        return False
    if q in text:
        return True
    if len(q) < MIN_FUZZY_LEN:
        return False
    return fuzz.partial_ratio(q, text) >= FUZZY_THRESHOLD


def verify_evidence(evidence: list[Evidence], packet: PacketText) -> list[Evidence]:
    pages = {p.page: p.markdown for p in packet.pages}
    out = []
    for e in evidence:
        found = next((c for c in (e.page, e.page - 1, e.page + 1)
                      if c in pages and quote_on_page(e.quote, pages[c])), None)
        out.append(e.model_copy(update={"page": found if found is not None else e.page,
                                        "verified": found is not None}))
    return out


def verify_results(results: list[RuleResult], packet: PacketText) -> list[RuleResult]:
    return [r.model_copy(update={"evidence": verify_evidence(r.evidence, packet)}) for r in results]


def verify_summary(summary: CaseSummary, packet: PacketText) -> CaseSummary:
    conflicts = [c.model_copy(update={"statements": verify_evidence(c.statements, packet)})
                 for c in summary.conflicts]
    return summary.model_copy(update={"conflicts": conflicts})
```

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python -m pytest tests/test_verify.py -v`
Expected: 8 passed. If `test_fuzzy_tolerates_small_ocr_differences` fails, print `fuzz.partial_ratio(...)`. Change the threshold only if it's ≥ 85 and the fabricated-quote test still passes.

- [ ] **Step 5: Commit**

```bash
git add app/rules/verify.py tests/test_verify.py
git commit -m "feat(rules): deterministic quote verification with off-by-one page correction"
```

---

### Task 7 (WS2): Decision tree

**Files:**
- Create: `app/rules/tree.py`, `tests/test_tree.py`

**Interfaces:**
- Consumes: `Rule`, `RuleResult` (already verified), `CaseSummary`, `BedOption`.
- Produces: `decide(rules: list[Rule], results: list[RuleResult], summary: CaseSummary, bed_options: list[BedOption]) -> Recommendation`. A pure function: no I/O, no LLM.

- [ ] **Step 1: Write the failing tests `tests/test_tree.py`**

```python
from app.rules.tree import decide
from app.schemas import Conflict, Evidence
from tests.factories import make_bed_option, make_result, make_rule, make_summary

RULES = [
    make_rule("GEN-1"), make_rule("GEN-2"), make_rule("GEN-3"),
    make_rule("MOB-3", "must"), make_rule("MH-2", "exclude"),
    make_rule("OTH-2", "note", human_review=True), make_rule("OTH-3", "conditional"),
    make_rule("DOC-6", "document", source="checklist"), make_rule("DM-Q1", "ask"),
]
BEDS = [make_bed_option("B1"), make_bed_option("B2")]


def results(**status):
    """Default: everything passes. Override with RULE_ID=status (use _ for -: MH_2='fail')."""
    base = {"GEN-1": "pass", "GEN-2": "fail", "GEN-3": "fail", "MOB-3": "pass", "MH-2": "pass",
            "OTH-2": "not_applicable", "OTH-3": "not_applicable", "DOC-6": "pass", "DM-Q1": "not_applicable"}
    base.update({k.replace("_", "-"): v for k, v in status.items()})
    return [make_result(k, v) for k, v in base.items()]


def node(rec, name):
    return next(s for s in rec.tree if s.node == name)


def test_all_pass_approves_and_holds_first_bed():
    rec = decide(RULES, results(), make_summary(), BEDS)
    assert rec.decision == "approve"
    assert rec.held_bed.bed_id == "B1" and [b.bed_id for b in rec.alternative_beds] == ["B2"]
    assert rec.headline.startswith("Recommend approve")
    assert all(s.outcome == "clear" for s in rec.tree if s.node != "bed")


def test_verified_exclusion_denies():
    rec = decide(RULES, results(MH_2="fail"), make_summary(), BEDS)
    assert rec.decision == "deny" and node(rec, "exclusions").rule_ids == ["MH-2"]
    assert rec.held_bed is None
    assert any("[MH-2]" in r for r in rec.top_reasons)


def test_unverified_exclusion_needs_review_not_deny():
    res = results()
    res[4] = make_result("MH-2", "fail", verified=False)
    rec = decide(RULES, res, make_summary(), BEDS)
    assert rec.decision == "needs_review"
    assert any(r.kind == "unverified_evidence" and r.rule_ids == ["MH-2"] for r in rec.review_reasons)


def test_must_fail_denies():
    rec = decide(RULES, results(MOB_3="fail"), make_summary(), BEDS)
    assert rec.decision == "deny" and node(rec, "must_rules").rule_ids == ["MOB-3"]


def test_unknown_must_needs_review_with_hospital_question():
    res = results()
    res[3] = make_result("MOB-3", "unknown", question="How far can the patient walk?")
    rec = decide(RULES, res, make_summary(), BEDS)
    assert rec.decision == "needs_review"
    assert rec.hospital_questions[0].question == "How far can the patient walk?"
    assert rec.held_bed.bed_id == "B1"  # bed stays held while info is gathered


def test_unknown_without_question_gets_default_from_rule_text():
    res = results()
    res[3] = make_result("MOB-3", "unknown")
    rec = decide(RULES, res, make_summary(), BEDS)
    assert "Text of MOB-3" in rec.hospital_questions[0].question


def test_human_review_rule_never_denies():
    rec = decide(RULES, results(OTH_2="fail"), make_summary(), BEDS)
    assert rec.decision == "needs_review"
    assert any(r.kind == "judgment_call" for r in rec.review_reasons)
    rec2 = decide(RULES, results(OTH_2="needs_human"), make_summary(), BEDS)
    assert rec2.decision == "needs_review"


def test_general_eligibility_all_fail_denies_unknown_reviews():
    rec = decide(RULES, results(GEN_1="fail"), make_summary(), BEDS)
    assert rec.decision == "deny" and node(rec, "general_eligibility").outcome == "triggered"
    rec2 = decide(RULES, results(GEN_1="unknown"), make_summary(), BEDS)
    assert rec2.decision == "needs_review"


def test_missing_document_needs_review():
    rec = decide(RULES, results(DOC_6="fail"), make_summary(), BEDS)
    assert rec.decision == "needs_review"
    assert any(r.kind == "missing_info" and "DOC-6" in r.rule_ids for r in rec.review_reasons)


def test_conditional_unknown_needs_review():
    rec = decide(RULES, results(OTH_3="unknown"), make_summary(), BEDS)
    assert rec.decision == "needs_review"


def test_ask_rules_never_block():
    rec = decide(RULES, results(DM_Q1="unknown"), make_summary(), BEDS)
    assert rec.decision == "approve"


def test_approve_without_bed_stays_approve_with_no_bed_reason():
    rec = decide(RULES, results(), make_summary(), [])
    assert rec.decision == "approve" and rec.held_bed is None
    assert any(r.kind == "no_bed" for r in rec.review_reasons)
    assert "no matching bed" in rec.headline


def test_conflict_on_flagged_rule_adds_reason_and_ignored_otherwise():
    conflict = Conflict(topic="Walking distance", statements=[Evidence(page=1, quote="a"), Evidence(page=2, quote="b")],
                        related_rule_ids=["MOB-3"], why_it_matters="Mobility rule")
    res = results()
    res[3] = make_result("MOB-3", "unknown")
    rec = decide(RULES, res, make_summary(conflicts=[conflict]), BEDS)
    assert any(r.kind == "conflict" for r in rec.review_reasons)
    rec2 = decide(RULES, results(), make_summary(conflicts=[conflict]), BEDS)
    assert rec2.decision == "approve"


def test_missing_result_for_rule_is_treated_as_not_evaluated():
    res = [r for r in results() if r.rule_id != "MH-2"]
    rec = decide(RULES, res, make_summary(), BEDS)
    assert rec.decision == "needs_review"
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python -m pytest tests/test_tree.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write `app/rules/tree.py`**

```python
"""Deterministic decision tree over verified rule results. Spec section 7.5. Pure function, no I/O."""
from app.schemas import (
    BedOption, CaseSummary, HospitalQuestion, Recommendation, ReviewReason, Rule, RuleResult, TreeStep,
)

GEN_IDS = ("GEN-1", "GEN-2", "GEN-3")
APPROVE_HIGHLIGHTS = ("GEN-1", "GEN-2", "GEN-3", "MOB-3", "MOB-4", "ADL-1", "COG-1", "COG-2")


def _verified(r: RuleResult) -> bool:
    return any(e.verified for e in r.evidence)


def _cite(r: RuleResult) -> str:
    pages = sorted({e.page for e in r.evidence if e.verified})
    return f" (p.{', '.join(map(str, pages))})" if pages else ""


def _first_sentence(text: str) -> str:
    return text.split(". ")[0].rstrip(".")


def _reason_line(r: RuleResult) -> str:
    return f"{_first_sentence(r.reasoning)}{_cite(r)} [{r.rule_id}]"


def _step(node: str, hits: list[RuleResult] | list[str], clear: str, triggered: str) -> TreeStep:
    ids = [h if isinstance(h, str) else h.rule_id for h in hits]
    return TreeStep(node=node, outcome="triggered" if ids else "clear", rule_ids=ids,
                    explanation=triggered if ids else clear)


def decide(rules: list[Rule], results: list[RuleResult], summary: CaseSummary,
           bed_options: list[BedOption]) -> Recommendation:
    meta = {r.id: r for r in rules}
    res = {r.rule_id: r for r in results if r.rule_id in meta}
    # Rules with no result at all are treated as not evaluated (unknown).
    for rule in rules:
        if rule.id not in res and rule.superseded_by is None:
            res[rule.id] = RuleResult(rule_id=rule.id, status="unknown", confidence="low",
                                      reasoning="Not evaluated; check manually.")
    kind = {rid: meta[rid].kind for rid in res}
    human = {rid for rid in res if meta[rid].human_review}

    steps: list[TreeStep] = []
    reasons: list[ReviewReason] = []
    questions: list[HospitalQuestion] = []

    # 1. exclusions
    excl = [r for r in res.values() if kind[r.rule_id] == "exclude" and r.rule_id not in human
            and r.status == "fail" and _verified(r)]
    steps.append(_step("exclusions", excl, "No exclusion found with verified evidence.",
                       "Exclusion present with verified evidence."))

    # 2. must rules (GEN-* handled separately)
    must_fail = [r for r in res.values() if kind[r.rule_id] == "must" and r.rule_id not in GEN_IDS
                 and r.rule_id not in human and r.status == "fail" and _verified(r)]
    steps.append(_step("must_rules", must_fail, "No required criterion clearly unmet.",
                       "Required criterion not met, with verified evidence."))

    # 3. general eligibility: at least one of GEN-1..3
    gen = [res[g] for g in GEN_IDS if g in res]
    gen_pass = any(g.status == "pass" for g in gen)
    gen_all_fail = bool(gen) and all(g.status == "fail" and _verified(g) for g in gen)
    gen_unknown = [g for g in gen if g.status == "unknown"]
    steps.append(TreeStep(
        node="general_eligibility", outcome="triggered" if (gen and not gen_pass and gen_all_fail) else "clear",
        rule_ids=[g.rule_id for g in gen] if not gen_pass else [],
        explanation="Meets at least one general admission criterion." if gen_pass else
        ("Meets none of the general admission criteria." if gen_all_fail else
         "General admission criteria not established from the packet.")))
    if gen and not gen_pass and not gen_all_fail:
        reasons.append(ReviewReason(kind="missing_info", rule_ids=[g.rule_id for g in gen_unknown] or list(GEN_IDS),
                                    detail="Housing / support situation that qualifies the patient is not established."))

    # 4. human review
    human_hits = [r for r in res.values() if r.status == "needs_human"
                  or (r.rule_id in human and r.status not in ("pass", "not_applicable"))]
    steps.append(_step("human_review", human_hits, "No case-by-case judgment needed.",
                       "IHH rules leave this to staff judgment."))
    for r in human_hits:
        reasons.append(ReviewReason(kind="judgment_call", rule_ids=[r.rule_id], detail=_first_sentence(r.reasoning)))

    # 5. missing information
    missing = [r for r in res.values() if r not in human_hits and r.rule_id not in GEN_IDS and (
        (kind[r.rule_id] in ("must", "exclude") and r.status == "unknown")
        or (kind[r.rule_id] == "conditional" and r.status in ("unknown", "fail"))
        or (kind[r.rule_id] == "document" and r.status in ("unknown", "fail")))]
    steps.append(_step("missing_info", missing, "Nothing required is missing.",
                       "Information needed to decide is missing from the packet."))
    for r in missing:
        reasons.append(ReviewReason(kind="missing_info", rule_ids=[r.rule_id], detail=_first_sentence(r.reasoning)))
        q = r.hospital_question or f"Please send or confirm: {meta[r.rule_id].text[:160]}"
        questions.append(HospitalQuestion(rule_id=r.rule_id, question=q))

    # 6. unverified failures can never deny
    unverified = [r for r in res.values() if kind[r.rule_id] in ("must", "exclude") and r.rule_id not in human
                  and r.rule_id not in GEN_IDS and r.status == "fail" and not _verified(r)]
    steps.append(_step("unverified", unverified, "All failing findings are backed by verified quotes.",
                       "A failing finding could not be matched to the packet text; check it by hand."))
    for r in unverified:
        reasons.append(ReviewReason(kind="unverified_evidence", rule_ids=[r.rule_id],
                                    detail=f"{_first_sentence(r.reasoning)} (quote not found on the cited page)"))

    # 7. conflicts touching anything flagged
    flagged = {r.rule_id for r in excl + must_fail + human_hits + missing + unverified}
    conflicts = [c for c in summary.conflicts if set(c.related_rule_ids) & flagged]
    steps.append(_step("conflicts", [i for c in conflicts for i in c.related_rule_ids],
                       "No contradictions affecting the decision.", "Pages of the packet disagree on this."))
    for c in conflicts:
        reasons.append(ReviewReason(kind="conflict", rule_ids=c.related_rule_ids, detail=f"{c.topic}: {c.why_it_matters}"))

    deny = bool(excl or must_fail or (gen and not gen_pass and gen_all_fail))
    decision = "deny" if deny else ("needs_review" if reasons else "approve")

    # Bed node
    held = None
    alternatives: list[BedOption] = []
    if decision != "deny":
        if bed_options:
            held, alternatives = bed_options[0], bed_options[1:]
        else:
            reasons.append(ReviewReason(kind="no_bed", rule_ids=[], detail="No available bed matches this patient's needs."))
    steps.append(TreeStep(node="bed", outcome="clear" if (held or decision == "deny") else "triggered",
                          rule_ids=[], explanation=f"Held bed {held.bed_id}." if held else
                          ("Not applicable (deny)." if decision == "deny" else "No matching bed available.")))

    if decision == "deny":
        hits = excl + must_fail or gen
        top = [_reason_line(r) for r in hits][:3]
        headline = f"Recommend deny: {len(excl) + len(must_fail) or 1} criterion not met"
    elif decision == "needs_review":
        top = [f"{rr.detail} [{', '.join(rr.rule_ids)}]" if rr.rule_ids else rr.detail for rr in reasons][:3]
        headline = f"Needs review: {len(reasons)} item(s) to resolve before deciding"
    else:
        top = [_reason_line(res[i]) for i in APPROVE_HIGHLIGHTS if i in res and res[i].status == "pass"][:3]
        headline = (f"Recommend approve: meets admission criteria; bed {held.label} in room {held.room} held"
                    if held else "Recommend approve: eligible, but no matching bed available")

    return Recommendation(decision=decision, headline=headline, top_reasons=top, review_reasons=reasons,
                          hospital_questions=questions, tree=steps, held_bed=held, alternative_beds=alternatives)
```

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python -m pytest tests/test_tree.py -v`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add app/rules/tree.py tests/test_tree.py
git commit -m "feat(rules): deterministic decision tree with review reasons and hospital questions"
```

---

### Task 8 (WS2): Prompts + parallel rule evaluation

**Files:**
- Create: `app/rules/prompts.py`, `app/rules/evaluate.py`, `tests/test_evaluate.py`, `docs/modules/rules-engine.md`

**Interfaces:**
- Consumes: `StructuredLLM.parse`/`FakeLLM`, `Criteria`, `PacketText`, `CaseSummary`, `RuleGroupOutput`, `RuleResult`, `LLMError`.
- Produces: `PROMPT_VERSION: str`; `async summarize(llm, packet, criteria) -> CaseSummary`; `async evaluate_group(llm, packet, criteria, group: str, rules: list[Rule]) -> list[RuleResult]`; `superseded_results(criteria) -> list[RuleResult]`; `async evaluate_packet(llm, packet, criteria, concurrency: int = 8) -> tuple[CaseSummary, list[RuleResult], dict[str, float]]`.

- [ ] **Step 1: Write `app/rules/prompts.py`**

```python
"""Prompts for case summary and rule evaluation. Bump PROMPT_VERSION whenever any text here changes."""

PROMPT_VERSION = "2026-10-17.1"

EVAL_SYSTEM = """You assist a referral coordinator at Illumination Health + Home (IHH). IHH runs recuperative care: short-term medical respite housing for people experiencing homelessness who are leaving the hospital but are too sick to recover on the street. You review a de-identified hospital referral packet against IHH's written admission criteria. A human coordinator makes every final decision. Your job is to find the facts and show your work so they can check it in seconds.

How to read the packet:
- It is a transcription of faxed pages. Each page starts with "## Page N — type". Cite page numbers exactly as given.
- The IHH referral form is one person's summary and can be wrong. Check the chart too: H&P, progress notes, PT/OT, nursing notes, labs, medication records. When the form and the chart disagree, say so and cite both.
- Some pages may come from an earlier hospital stay. Say so when a page looks older or contradicts the current admission.
- [REDACTED] marks text removed for privacy. It is never evidence that something is missing, negative or normal.
- Dates are redacted. A rule that depends on a number of hours or days (24 hours on insulin, 72 hours after alcohol intoxication, a COVID swab within 24 hours, BP stable for 24 hours) is unknown unless the packet states the interval in words.
- Something the packet does not mention is unknown, not normal.
- Never invent facts, values, quotes or page numbers. Quotes must be copied character for character from the transcription.

Which rules win: IHH's internal admission criteria (IDs like GEN-, ADL-, MOB-, MH-, COG-, OTH-, INF-, and the body-system lists) win over the hospital-facing referral form (FORM-). The precedence notes and known gaps are below the packet."""

KNOWN_GAPS = """Places where IHH's written rules are unclear (show the gap instead of guessing):
- Registered sex offenders: case by case; offenses involving violence likely denied. Never deny automatically: needs_human.
- Wheelchair users: a wheelchair-dependent patient who transfers independently and uses the chair independently is eligible (MOB-3/MOB-4 win over the form).
- Tuberculosis and refusing detox: the referral form's exclusion applies.
- "O2 baseline of 94% or greater with physician explanation": meaning in practice not stated.
- "FSP": likely Full Service Partnership, a county mental-health program.
- Insulin "less than 24 hours prior to admission": unclear whether the clock starts at the first dose or when doses stop changing.
- DKA: unclear whether resolved DKA still excludes. C. diff: unclear whether exclusion ends when isolation lifts or when treatment finishes.
- Troponin after a heart attack: a patient admitted for a heart attack will not have two negative troponins, yet history of heart attack is eligible.
- COVID swab "within 24 hours of admission": a swab negative at referral may be older than 24 hours at arrival.
- "Complicated large abscess" is not defined. The hard-to-reach wound list seems to be missing an item; sacrum is not named.
- Home health wording varies by rule: "established", "ordered", "coordinated", "coordinated and scheduled".
- There is no written "re-refer when ready" outcome."""

SUMMARY_TASK = """Summarize this packet for the coordinator. Use only the packet; use null or "unknown" when something is not stated.
- one_liner: one sentence, with no names, dates or identifiers.
- has_ihh_referral_form: whether the IHH Recuperative Care Referral Form is in the packet.
- documents_present: for each referral-checklist item that is present, its ID and the pages where it appears. Checklist:
{checklist}
- key_findings: up to 8 short facts that matter for eligibility, each ending with its page, e.g. "Walks 150 ft with a front-wheeled walker (p.12)".
- conflicts: places where pages disagree about something that matters (age, mobility, cognition, living situation, medication plan, infection or isolation status). Each needs at least two evidence items with verbatim quotes and the related rule IDs.
- bed_needs: sex as documented; wheelchair true only if wheelchair dependent; mobility_aid if any; oxygen true if oxygen is planned after discharge; isolation true if isolation precautions continue at discharge."""

RULES_TASK = """Evaluate every rule below for this patient. Return exactly one result per rule ID listed, and no other IDs.

Statuses are always from the eligibility point of view:
- must: pass = clearly met; fail = clearly not met; unknown = the packet does not establish it. If the rule starts with a condition that does not apply (e.g. "If using DME" for a patient with no DME), not_applicable.
- exclude: fail = the excluding condition IS present. pass = the chart addresses the area and the condition is absent (e.g. a mental-status exam with "denies SI"). For body-system ineligible conditions, not_applicable when the patient has no condition in that body system. unknown only when the area matters for this patient and the chart is silent.
- document: pass = the document or data is in the packet; fail = it is not; not_applicable when the rule only applies to a situation this patient is not in (e.g. glucose readings for a patient without diabetes).
- conditional: not_applicable if the situation does not apply (no IV, no wound, no catheter); pass if the required arrangement is confirmed in the packet; unknown if it is needed but not confirmed; fail only if the packet says it cannot be arranged.
- eligible: pass if the patient has this listed eligible condition; otherwise not_applicable.
- ask: not_applicable, with the answer from the packet in `answer` ("not stated" if absent).
- note: needs_human if it applies to this patient, otherwise not_applicable.
- needs_human: any case-by-case judgment the rules leave to staff (registered sex offender status, neurosurgery escalation). Never use fail for OTH-2.

For every pass or fail, give at least one evidence item: the page number and a short quote copied exactly from that page. For unknown, write a hospital_question: one specific, polite question the coordinator can ask the hospital to resolve it. Keep reasoning to 1-3 plain sentences a non-clinician can follow. confidence: high when the chart is explicit, medium when you combined several notes, low when the evidence is thin or conflicting.

Rule group: {group}
Rules (ID [kind] (section): text):
{rules}"""
```

- [ ] **Step 2: Write the failing tests `tests/test_evaluate.py`**

```python
import re

from app.llm import LLMError
from app.rules.criteria import load_criteria
from app.rules.evaluate import evaluate_group, evaluate_packet, superseded_results
from app.schemas import CaseSummary, RuleGroupOutput, RuleResult
from tests.factories import make_packet, make_rule, make_summary
from tests.fakes import FakeLLM

RULE_LINE = re.compile(r"^- (\S+) \[", re.M)


def ids_in(content) -> list[str]:
    return RULE_LINE.findall(content[-1]["text"])


def happy(output, system, content):
    if output is CaseSummary:
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
    assert llm.calls[0]["output"] is CaseSummary
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
```

- [ ] **Step 3: Run to verify failure**

Run: `.venv/Scripts/python -m pytest tests/test_evaluate.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 4: Write `app/rules/evaluate.py`**

```python
"""Case summary + parallel rule-group evaluation with Claude. Every rule gets exactly one result."""
import asyncio
import logging
import time

from app.llm import LLMError
from app.rules.criteria import Criteria
from app.rules.prompts import EVAL_SYSTEM, KNOWN_GAPS, RULES_TASK, SUMMARY_TASK
from app.schemas import CaseSummary, PacketText, Rule, RuleGroupOutput, RuleResult

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
    return await llm.parse(system=EVAL_SYSTEM, content=content, output=CaseSummary, effort="high", max_tokens=16000)


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


async def evaluate_group(llm, packet: PacketText, criteria: Criteria, group: str, rules: list[Rule],
                         _depth: int = 0) -> list[RuleResult]:
    content = [packet_block(packet, criteria),
               {"type": "text", "text": RULES_TASK.format(group=group, rules=_rule_lines(rules))}]
    try:
        out = await llm.parse(system=EVAL_SYSTEM, content=content, output=RuleGroupOutput,
                              effort="high", max_tokens=20000)
        return _ensure_coverage(rules, out.results)
    except LLMError as e:
        if len(rules) > SPLIT_THRESHOLD and _depth == 0:
            log.warning("Group %s failed (%s); splitting in half and retrying", group, e)
            mid = len(rules) // 2
            a, b = await asyncio.gather(
                evaluate_group(llm, packet, criteria, f"{group}-1", rules[:mid], _depth + 1),
                evaluate_group(llm, packet, criteria, f"{group}-2", rules[mid:], _depth + 1))
            return a + b
        log.error("Group %s failed: %s", group, e)
        return _ensure_coverage(rules, [])


def superseded_results(criteria: Criteria) -> list[RuleResult]:
    return [RuleResult(rule_id=r.id, status="not_applicable", confidence="high",
                       reasoning=f"Superseded by {r.superseded_by}: IHH's internal admission criteria win over the referral form.")
            for r in criteria.superseded()]


async def evaluate_packet(llm, packet: PacketText, criteria: Criteria,
                          concurrency: int = 8) -> tuple[CaseSummary, list[RuleResult], dict[str, float]]:
    t0 = time.perf_counter()
    summary = await summarize(llm, packet, criteria)  # first: warms the prompt cache
    t1 = time.perf_counter()
    sem = asyncio.Semaphore(concurrency)

    async def run(group: str, rules: list[Rule]) -> list[RuleResult]:
        async with sem:
            return await evaluate_group(llm, packet, criteria, group, rules)

    groups = criteria.groups()
    chunks = await asyncio.gather(*(run(g, rules) for g, rules in groups.items() if rules))
    results = [r for chunk in chunks for r in chunk] + superseded_results(criteria)
    return summary, results, {"summary": t1 - t0, "rules": time.perf_counter() - t1}
```

- [ ] **Step 5: Run tests**

Run: `.venv/Scripts/python -m pytest tests/test_evaluate.py tests/test_criteria.py tests/test_verify.py tests/test_tree.py -v`
Expected: all passed

- [ ] **Step 6: Write `docs/modules/rules-engine.md`**

```markdown
# Rules engine (`app/rules/`)

**Purpose:** turn `PacketText` into a cited, rule-by-rule judgment and a deterministic recommendation.

**Flow:** `evaluate_packet` (Claude: 1 summary call + 8 parallel rule-group calls, shared cached prefix) →
`verify_results` / `verify_summary` (deterministic quote check) → `decide` (deterministic tree).

**Files**
- `criteria.py`: 155 rules from `criteria/admission-criteria.json` (51 outline incl. GEN-1..3, 81 body-system,
  10 checklist, 13 referral-form; 3 form rules superseded → fixed `not_applicable`). 8 groups of ≤30 rules.
- `prompts.py`: all prompt text; **bump `PROMPT_VERSION` on any change**.
- `evaluate.py`: coverage guarantee (missing → `unknown`/low confidence, invented IDs dropped). A failed group with
  more than 6 rules is split in half and retried once.
- `verify.py`: normalized substring, or rapidfuzz `partial_ratio >= 90` for quotes of 12+ chars. Checks the cited page ±1.
- `tree.py`: order = exclusions → must → general eligibility → human review → missing info → unverified →
  conflicts → bed. Deny needs **verified** evidence. OTH-2 never denies. `ask` never blocks.

**Status semantics:** always from the eligibility point of view (see `app/schemas.py` and `RULES_TASK`).

**Testing:** `pytest tests/test_criteria.py tests/test_verify.py tests/test_tree.py tests/test_evaluate.py`.

**Known limits / tuning log:** add findings from eval runs here (rule-ID terms only, never packet quotes).
```

- [ ] **Step 7: Commit**

```bash
git add app/rules/prompts.py app/rules/evaluate.py tests/test_evaluate.py docs/modules/rules-engine.md
git commit -m "feat(rules): prompts and parallel rule evaluation with coverage and split-retry"
```

---

### Task 9 (WS3): Bed tables + service

**Files:**
- Create: `app/beds/models.py`, `app/beds/service.py`, `tests/test_beds_service.py`

**Interfaces:**
- Consumes: `BedNeeds`, `BedOption`, `BedView`, `BedSummary`, `SiteCount`, `AssignmentView` from `app.schemas`; `make_engine("sqlite://")`.
- Produces (all take `session: Session` first): `class BedUnavailable(Exception)`, `class BedNotFound(Exception)`, `utcnow() -> datetime`, `effective_status(session, bed) -> str`, `find_matches(session, needs, limit=3, for_referral=None) -> list[BedOption]`, `hold(session, bed_id, referral_id, ttl_hours) -> Hold`, `release_hold(session, referral_id) -> None`, `held_bed_id(session, referral_id) -> str | None`, `assign(session, bed_id, referral_id, client_label, expected_end_date=None) -> Assignment`, `discharge(session, assignment_id) -> Assignment`, `create_bed(session, data: BedCreate) -> Bed`, `update_bed(session, bed_id, data: BedUpdate) -> Bed`, `to_view(session, bed) -> BedView`, `list_beds(session, site=None, status=None) -> list[BedView]`, `summary(session) -> BedSummary`, `list_assignments(session, active_only=True) -> list[AssignmentView]`, `bed_option(session, bed, notes=None) -> BedOption`.

- [ ] **Step 1: Write `app/beds/models.py`**

```python
"""Bed management tables. Stored bed.status is available|occupied|out_of_service; 'held' is derived from Hold."""
from datetime import date, datetime

from sqlmodel import Field, SQLModel


class Site(SQLModel, table=True):
    id: str = Field(primary_key=True)
    name: str


class Bed(SQLModel, table=True):
    id: str = Field(primary_key=True)
    site_id: str = Field(foreign_key="site.id", index=True)
    room: str
    label: str
    sex_designation: str = "any"
    wheelchair_accessible: bool = False
    ground_floor: bool = False
    oxygen_ok: bool = False
    isolation_ok: bool = False
    status: str = "available"
    notes: str = ""


class Hold(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    bed_id: str = Field(foreign_key="bed.id", index=True)
    referral_id: str = Field(index=True)
    created_at: datetime
    expires_at: datetime
    active: bool = True


class Assignment(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    bed_id: str = Field(foreign_key="bed.id", index=True)
    referral_id: str = Field(index=True)
    client_label: str
    start_date: date
    expected_end_date: date | None = None
    released_at: datetime | None = None
    status: str = "active"
```

- [ ] **Step 2: Write the failing tests `tests/test_beds_service.py`**

```python
from datetime import timedelta

import pytest
from sqlmodel import Session, SQLModel

import app.beds.models  # noqa: F401
from app.beds import service
from app.beds.models import Bed, Hold, Site
from app.db import make_engine
from app.schemas import BedCreate, BedNeeds, BedUpdate


@pytest.fixture
def session():
    engine = make_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        s.add(Site(id="sa", name="Site A (demo)"))
        s.add_all([
            Bed(id="M1", site_id="sa", room="101", label="A", sex_designation="male"),
            Bed(id="M2", site_id="sa", room="102", label="A", sex_designation="male", wheelchair_accessible=True, ground_floor=True),
            Bed(id="F1", site_id="sa", room="201", label="A", sex_designation="female"),
            Bed(id="ANY", site_id="sa", room="301", label="A", sex_designation="any", oxygen_ok=True),
            Bed(id="OOS", site_id="sa", room="401", label="A", sex_designation="male", status="out_of_service"),
        ])
        s.commit()
        yield s


def needs(**kw):
    base = dict(sex="male", wheelchair=False, mobility_aid=None, oxygen=False, isolation=False)
    base.update(kw)
    return BedNeeds(**base)


def test_match_prefers_least_specialized_compatible_bed(session):
    ids = [o.bed_id for o in service.find_matches(session, needs())]
    assert ids[0] == "M1" and "F1" not in ids and "OOS" not in ids


def test_wheelchair_requires_accessible(session):
    assert [o.bed_id for o in service.find_matches(session, needs(wheelchair=True))] == ["M2"]


def test_unknown_sex_only_any_beds(session):
    opts = service.find_matches(session, needs(sex="unknown"))
    assert [o.bed_id for o in opts] == ["ANY"]
    assert any("not stated" in n for n in opts[0].match_notes)


def test_oxygen_requires_oxygen_ok(session):
    # F1 (female) has no oxygen; ANY (any-gender, oxygen_ok) is the only match
    assert [o.bed_id for o in service.find_matches(session, needs(sex="female", oxygen=True))] == ["ANY"]


def test_hold_hides_bed_from_others_but_not_owner(session):
    service.hold(session, "M1", "R1", ttl_hours=4)
    assert "M1" not in [o.bed_id for o in service.find_matches(session, needs(), for_referral="R2")]
    assert "M1" in [o.bed_id for o in service.find_matches(session, needs(), for_referral="R1")]
    assert service.held_bed_id(session, "R1") == "M1"
    view = next(b for b in service.list_beds(session) if b.id == "M1")
    assert view.status == "held" and view.referral_id == "R1"


def test_expired_hold_frees_bed(session):
    h = service.hold(session, "M1", "R1", ttl_hours=4)
    h.expires_at = service.utcnow() - timedelta(minutes=1)
    session.add(h)
    session.commit()
    assert "M1" in [o.bed_id for o in service.find_matches(session, needs(), for_referral="R2")]


def test_new_hold_replaces_old_hold_for_same_referral(session):
    service.hold(session, "M1", "R1", ttl_hours=4)
    service.hold(session, "M2", "R1", ttl_hours=4)
    assert service.held_bed_id(session, "R1") == "M2"


def test_hold_on_unavailable_bed_raises(session):
    service.hold(session, "M1", "R1", ttl_hours=4)
    with pytest.raises(service.BedUnavailable):
        service.hold(session, "M1", "R2", ttl_hours=4)


def test_assign_converts_hold_and_blocks_double_booking(session):
    service.hold(session, "M1", "R1", ttl_hours=4)
    a = service.assign(session, "M1", "R1", client_label="R1")
    assert a.status == "active"
    assert session.get(Bed, "M1").status == "occupied"
    assert service.held_bed_id(session, "R1") is None
    with pytest.raises(service.BedUnavailable):
        service.assign(session, "M1", "R2", client_label="R2")


def test_assign_bed_held_by_someone_else_raises(session):
    service.hold(session, "M1", "R1", ttl_hours=4)
    with pytest.raises(service.BedUnavailable):
        service.assign(session, "M1", "R2", client_label="R2")


def test_discharge_frees_bed(session):
    a = service.assign(session, "M1", "R1", client_label="R1")
    service.discharge(session, a.id)
    assert session.get(Bed, "M1").status == "available"
    assert service.list_assignments(session, active_only=True) == []


def test_summary_counts(session):
    service.hold(session, "M1", "R1", ttl_hours=4)
    service.assign(session, "F1", "R2", client_label="R2")
    s = service.summary(session)
    assert (s.total, s.available, s.held, s.occupied, s.out_of_service) == (5, 2, 1, 1, 1)
    assert s.by_site[0].site == "Site A (demo)"


def test_create_and_update_bed(session):
    b = service.create_bed(session, BedCreate(site="Site B (demo)", room="1", label="A"))
    assert b.id == "site-b-demo-1-a"
    service.update_bed(session, b.id, BedUpdate(status="out_of_service"))
    assert service.to_view(session, session.get(Bed, b.id)).status == "out_of_service"
    with pytest.raises(service.BedNotFound):
        service.update_bed(session, "nope", BedUpdate(notes="x"))
```

- [ ] **Step 3: Run to verify failure**

Run: `.venv/Scripts/python -m pytest tests/test_beds_service.py -v`
Expected: FAIL with `ImportError` / `ModuleNotFoundError` for `app.beds.service`

- [ ] **Step 4: Write `app/beds/service.py`**

```python
"""Bed management service. The ONLY interface other modules use for beds."""
import re
from datetime import date, datetime, timedelta, timezone

from sqlmodel import Session, select

from app.beds.models import Assignment, Bed, Hold, Site
from app.schemas import (
    AssignmentView, BedCreate, BedNeeds, BedOption, BedSummary, BedUpdate, BedView, SiteCount,
)


class BedUnavailable(Exception):
    pass


class BedNotFound(Exception):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def _active_hold(session: Session, bed_id: str) -> Hold | None:
    now = utcnow()
    return session.exec(select(Hold).where(Hold.bed_id == bed_id, Hold.active == True,  # noqa: E712
                                           Hold.expires_at > now)).first()


def _active_assignment(session: Session, bed_id: str) -> Assignment | None:
    return session.exec(select(Assignment).where(Assignment.bed_id == bed_id, Assignment.status == "active")).first()


def effective_status(session: Session, bed: Bed) -> str:
    if bed.status != "available":
        return bed.status
    return "held" if _active_hold(session, bed.id) else "available"


def _is_available(session: Session, bed: Bed, for_referral: str | None) -> bool:
    if bed.status != "available":
        return False
    h = _active_hold(session, bed.id)
    return h is None or (for_referral is not None and h.referral_id == for_referral)


def _site_name(session: Session, site_id: str) -> str:
    site = session.get(Site, site_id)
    return site.name if site else site_id


def bed_option(session: Session, bed: Bed, notes: list[str] | None = None) -> BedOption:
    return BedOption(bed_id=bed.id, site=_site_name(session, bed.site_id), room=bed.room, label=bed.label,
                     match_notes=notes or [])


def _compatible(bed: Bed, needs: BedNeeds) -> list[str] | None:
    notes: list[str] = []
    if needs.sex == "unknown":
        if bed.sex_designation != "any":
            return None
        notes.append("any-gender room (patient sex not stated in packet)")
    elif bed.sex_designation not in ("any", needs.sex):
        return None
    else:
        notes.append(f"{bed.sex_designation} room" if bed.sex_designation != "any" else "any-gender room")
    if needs.wheelchair:
        if not bed.wheelchair_accessible:
            return None
        notes.append("wheelchair accessible")
    if needs.oxygen:
        if not bed.oxygen_ok:
            return None
        notes.append("oxygen OK")
    if needs.isolation:
        if not bed.isolation_ok:
            return None
        notes.append("isolation capable")
    if needs.mobility_aid and bed.ground_floor:
        notes.append("ground floor")
    return notes


def find_matches(session: Session, needs: BedNeeds, limit: int = 3, for_referral: str | None = None) -> list[BedOption]:
    candidates = []
    for bed in session.exec(select(Bed)).all():
        if not _is_available(session, bed, for_referral):
            continue
        notes = _compatible(bed, needs)
        if notes is None:
            continue
        specialization = sum([bed.wheelchair_accessible, bed.oxygen_ok, bed.isolation_ok])
        wants_ground = bool(needs.mobility_aid or needs.wheelchair)
        candidates.append(((specialization, 0 if (wants_ground and bed.ground_floor) else 1,
                            bed.site_id, bed.room, bed.label), bed, notes))
    candidates.sort(key=lambda c: c[0])
    return [bed_option(session, bed, notes) for _, bed, notes in candidates[:limit]]


def release_hold(session: Session, referral_id: str) -> None:
    for h in session.exec(select(Hold).where(Hold.referral_id == referral_id, Hold.active == True)).all():  # noqa: E712
        h.active = False
        session.add(h)
    session.commit()


def held_bed_id(session: Session, referral_id: str) -> str | None:
    h = session.exec(select(Hold).where(Hold.referral_id == referral_id, Hold.active == True,  # noqa: E712
                                        Hold.expires_at > utcnow())).first()
    return h.bed_id if h else None


def hold(session: Session, bed_id: str, referral_id: str, ttl_hours: float) -> Hold:
    bed = session.get(Bed, bed_id)
    if bed is None:
        raise BedNotFound(bed_id)
    if not _is_available(session, bed, referral_id):
        raise BedUnavailable(f"Bed {bed_id} is not available")
    release_hold(session, referral_id)
    now = utcnow()
    h = Hold(bed_id=bed_id, referral_id=referral_id, created_at=now, expires_at=now + timedelta(hours=ttl_hours))
    session.add(h)
    session.commit()
    session.refresh(h)
    return h


def assign(session: Session, bed_id: str, referral_id: str, client_label: str,
           expected_end_date: date | None = None) -> Assignment:
    bed = session.get(Bed, bed_id)
    if bed is None:
        raise BedNotFound(bed_id)
    if not _is_available(session, bed, referral_id) or _active_assignment(session, bed_id):
        raise BedUnavailable(f"Bed {bed_id} is no longer available")
    for h in session.exec(select(Hold).where(Hold.referral_id == referral_id, Hold.active == True)).all():  # noqa: E712
        h.active = False
        session.add(h)
    bed.status = "occupied"
    session.add(bed)
    a = Assignment(bed_id=bed_id, referral_id=referral_id, client_label=client_label,
                   start_date=utcnow().date(), expected_end_date=expected_end_date)
    session.add(a)
    session.commit()
    session.refresh(a)
    return a


def discharge(session: Session, assignment_id: int) -> Assignment:
    a = session.get(Assignment, assignment_id)
    if a is None:
        raise BedNotFound(f"assignment {assignment_id}")
    a.status, a.released_at = "released", utcnow()
    bed = session.get(Bed, a.bed_id)
    bed.status = "available"
    session.add_all([a, bed])
    session.commit()
    session.refresh(a)
    return a


def _ensure_site(session: Session, name: str) -> Site:
    site_id = slug(name)
    site = session.get(Site, site_id)
    if site is None:
        site = Site(id=site_id, name=name)
        session.add(site)
        session.commit()
    return site


def create_bed(session: Session, data: BedCreate) -> Bed:
    site = _ensure_site(session, data.site)
    bed_id = data.id or slug(f"{data.site}-{data.room}-{data.label}")
    bed = Bed(id=bed_id, site_id=site.id, room=data.room, label=data.label,
              **data.model_dump(exclude={"id", "site", "room", "label"}))
    session.add(bed)
    session.commit()
    session.refresh(bed)
    return bed


def update_bed(session: Session, bed_id: str, data: BedUpdate) -> Bed:
    bed = session.get(Bed, bed_id)
    if bed is None:
        raise BedNotFound(bed_id)
    for k, v in data.model_dump(exclude_none=True).items():
        setattr(bed, k, v)
    session.add(bed)
    session.commit()
    session.refresh(bed)
    return bed


def to_view(session: Session, bed: Bed) -> BedView:
    h = _active_hold(session, bed.id)
    a = _active_assignment(session, bed.id)
    return BedView(id=bed.id, site=_site_name(session, bed.site_id), room=bed.room, label=bed.label,
                   sex_designation=bed.sex_designation, wheelchair_accessible=bed.wheelchair_accessible,
                   ground_floor=bed.ground_floor, oxygen_ok=bed.oxygen_ok, isolation_ok=bed.isolation_ok,
                   status=effective_status(session, bed), notes=bed.notes,
                   referral_id=(a.referral_id if a else (h.referral_id if h else None)),
                   assignment_id=a.id if a else None, hold_expires_at=h.expires_at if h else None)


def list_beds(session: Session, site: str | None = None, status: str | None = None) -> list[BedView]:
    beds = session.exec(select(Bed).order_by(Bed.site_id, Bed.room, Bed.label)).all()
    views = [to_view(session, b) for b in beds]
    if site:
        views = [v for v in views if v.site == site or slug(v.site) == slug(site)]
    if status:
        views = [v for v in views if v.status == status]
    return views


def summary(session: Session) -> BedSummary:
    views = list_beds(session)
    count = lambda st: sum(1 for v in views if v.status == st)  # noqa: E731
    sites: dict[str, SiteCount] = {}
    for v in views:
        sc = sites.setdefault(v.site, SiteCount(site=v.site, total=0, available=0))
        sc.total += 1
        sc.available += v.status == "available"
    return BedSummary(total=len(views), available=count("available"), held=count("held"),
                      occupied=count("occupied"), out_of_service=count("out_of_service"), by_site=list(sites.values()))


def list_assignments(session: Session, active_only: bool = True) -> list[AssignmentView]:
    q = select(Assignment)
    if active_only:
        q = q.where(Assignment.status == "active")
    return [AssignmentView(**a.model_dump()) for a in session.exec(q).all()]
```

- [ ] **Step 5: Run tests**

Run: `.venv/Scripts/python -m pytest tests/test_beds_service.py -v`
Expected: all passed

- [ ] **Step 6: Commit**

```bash
git add app/beds/models.py app/beds/service.py tests/test_beds_service.py
git commit -m "feat(beds): bed tables and service: matching, holds, assignments, summary"
```

---

### Task 10 (WS3): Bed import (CSV/XLSX) + demo seed

**Files:**
- Create: `app/beds/importer.py`, `app/beds/seed.py`, `tests/test_beds_import.py`

**Interfaces:**
- Consumes: `service.create_bed`, `service.slug`, `Bed`, `Site`, `BedCreate`, `BedImportReport`.
- Produces: `DEFAULT_MAPPING: dict[str, str]`; `read_rows(filename: str, data: bytes) -> list[dict[str, str]]`; `import_beds(session, filename: str, data: bytes, mapping: dict[str, str] | None = None) -> BedImportReport`; `seed_demo_beds(session) -> int` (number created; 0 if beds exist).

- [ ] **Step 1: Write the failing tests `tests/test_beds_import.py`**

```python
import io

import openpyxl
import pytest
from sqlmodel import Session, SQLModel, select

import app.beds.models  # noqa: F401
from app.beds.importer import import_beds, read_rows
from app.beds.models import Bed
from app.beds.seed import seed_demo_beds
from app.db import make_engine

CSV = b"""Site,Room,Bed,Gender,Wheelchair,O2,Status,Notes
Demo North,101,A,M,yes,,Available,
Demo North,101,B,M,no,yes,Occupied,
Demo North,,C,F,,,Available,missing room
"""


@pytest.fixture
def session():
    engine = make_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def test_csv_import_maps_columns_and_reports_errors(session):
    report = import_beds(session, "beds.csv", CSV)
    assert report.created == 2 and report.updated == 0
    assert len(report.errors) == 1 and "row 4" in report.errors[0]
    a = session.get(Bed, "demo-north-101-a")
    assert a.sex_designation == "male" and a.wheelchair_accessible and not a.oxygen_ok
    b = session.get(Bed, "demo-north-101-b")
    assert b.status == "occupied" and b.oxygen_ok


def test_reimport_updates(session):
    import_beds(session, "beds.csv", CSV)
    report = import_beds(session, "beds.csv", CSV.replace(b"Occupied", b"Available"))
    assert report.updated == 2 and report.created == 0
    assert session.get(Bed, "demo-north-101-b").status == "available"


def test_xlsx_import(session):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Site", "Room", "Bed", "Gender"])
    ws.append(["Demo South", 5, "A", "Female"])
    buf = io.BytesIO()
    wb.save(buf)
    assert read_rows("beds.xlsx", buf.getvalue())[0]["room"] == "5"
    report = import_beds(session, "beds.xlsx", buf.getvalue())
    assert report.created == 1 and session.get(Bed, "demo-south-5-a").sex_designation == "female"


def test_unsupported_file_type(session):
    report = import_beds(session, "beds.txt", b"x")
    assert report.created == 0 and "Unsupported" in report.errors[0]


def test_seed_is_idempotent(session):
    n = seed_demo_beds(session)
    assert n >= 16
    assert seed_demo_beds(session) == 0
    beds = session.exec(select(Bed)).all()
    assert any(b.wheelchair_accessible for b in beds) and any(b.status == "occupied" for b in beds)
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python -m pytest tests/test_beds_import.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write `app/beds/importer.py`**

```python
"""Import beds from IHH's spreadsheet (CSV or XLSX). Header names are matched case-insensitively via DEFAULT_MAPPING."""
import csv
import io

import openpyxl
from sqlmodel import Session

from app.beds.models import Bed
from app.beds.service import create_bed, slug
from app.schemas import BedCreate, BedImportReport

DEFAULT_MAPPING = {
    "id": "id", "bed id": "id", "bed_id": "id",
    "site": "site", "location": "site", "facility": "site",
    "room": "room", "room #": "room", "room number": "room",
    "bed": "label", "label": "label", "bed label": "label",
    "gender": "sex_designation", "sex": "sex_designation",
    "wheelchair": "wheelchair_accessible", "ada": "wheelchair_accessible", "accessible": "wheelchair_accessible",
    "ground floor": "ground_floor", "first floor": "ground_floor",
    "oxygen": "oxygen_ok", "o2": "oxygen_ok",
    "isolation": "isolation_ok",
    "status": "status", "notes": "notes", "comments": "notes",
}
TRUE = {"y", "yes", "true", "1", "x"}


def _bool(v: str) -> bool:
    return str(v).strip().lower() in TRUE


def _sex(v: str) -> str:
    s = str(v).strip().lower()
    if s in ("m", "male", "men", "man"):
        return "male"
    if s in ("f", "female", "women", "woman"):
        return "female"
    return "any"


def _status(v: str) -> str:
    s = str(v).strip().lower()
    if s in ("occupied", "assigned", "in use", "taken"):
        return "occupied"
    if s in ("oos", "out of service", "out_of_service", "closed", "maintenance"):
        return "out_of_service"
    return "available"


def read_rows(filename: str, data: bytes) -> list[dict[str, str]]:
    name = filename.lower()
    if name.endswith(".csv"):
        rows = list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))
    elif name.endswith(".xlsx"):
        ws = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True).active
        values = list(ws.iter_rows(values_only=True))
        headers = [str(h or "").strip() for h in values[0]] if values else []
        rows = [{headers[i]: ("" if v is None else str(v)) for i, v in enumerate(r) if i < len(headers)}
                for r in values[1:]]
    else:
        raise ValueError(f"Unsupported file type: {filename} (use .csv or .xlsx)")
    return [{(k or "").strip().lower(): (v or "").strip() for k, v in r.items()} for r in rows]


def import_beds(session: Session, filename: str, data: bytes, mapping: dict[str, str] | None = None) -> BedImportReport:
    mapping = {k.lower(): v for k, v in (mapping or DEFAULT_MAPPING).items()}
    try:
        raw_rows = read_rows(filename, data)
    except ValueError as e:
        return BedImportReport(created=0, updated=0, errors=[str(e)])
    created = updated = 0
    errors: list[str] = []
    for n, raw in enumerate(raw_rows, start=2):  # row 1 is the header
        row = {mapping[k]: v for k, v in raw.items() if k in mapping}
        if not row.get("site") or not row.get("room") or not row.get("label"):
            errors.append(f"row {n}: site, room and bed label are required")
            continue
        fields = dict(
            sex_designation=_sex(row.get("sex_designation", "")),
            wheelchair_accessible=_bool(row.get("wheelchair_accessible", "")),
            ground_floor=_bool(row.get("ground_floor", "")),
            oxygen_ok=_bool(row.get("oxygen_ok", "")),
            isolation_ok=_bool(row.get("isolation_ok", "")),
            notes=row.get("notes", ""),
        )
        bed_id = row.get("id") or slug(f"{row['site']}-{row['room']}-{row['label']}")
        existing = session.get(Bed, bed_id)
        if existing:
            for k, v in fields.items():
                setattr(existing, k, v)
            existing.status = _status(row.get("status", ""))
            session.add(existing)
            session.commit()
            updated += 1
        else:
            bed = create_bed(session, BedCreate(id=bed_id, site=row["site"], room=row["room"], label=row["label"], **fields))
            bed.status = _status(row.get("status", ""))
            session.add(bed)
            session.commit()
            created += 1
    return BedImportReport(created=created, updated=updated, errors=errors)
```

- [ ] **Step 4: Write `app/beds/seed.py`**

```python
"""INVENTED demo beds so the prototype works before IHH's real sheet is imported."""
from sqlmodel import Session, select

from app.beds.models import Bed
from app.beds.service import create_bed
from app.schemas import BedCreate

DEMO = [
    # site, room, label, sex, wheelchair, ground, oxygen, isolation, status
    ("Demo Site A", "101", "A", "male", True, True, False, False, "available"),
    ("Demo Site A", "101", "B", "male", False, True, False, False, "occupied"),
    ("Demo Site A", "102", "A", "male", False, False, False, False, "available"),
    ("Demo Site A", "102", "B", "male", False, False, True, False, "available"),
    ("Demo Site A", "103", "A", "male", False, False, False, False, "occupied"),
    ("Demo Site A", "103", "B", "male", False, False, False, False, "available"),
    ("Demo Site A", "201", "A", "female", True, True, False, False, "available"),
    ("Demo Site A", "201", "B", "female", False, True, False, False, "occupied"),
    ("Demo Site A", "202", "A", "female", False, False, True, False, "available"),
    ("Demo Site A", "202", "B", "female", False, False, False, False, "available"),
    ("Demo Site A", "301", "A", "any", False, True, False, True, "available"),
    ("Demo Site B", "1", "A", "male", False, True, False, False, "available"),
    ("Demo Site B", "1", "B", "male", False, True, False, False, "occupied"),
    ("Demo Site B", "2", "A", "male", True, True, True, False, "available"),
    ("Demo Site B", "3", "A", "female", False, True, False, False, "available"),
    ("Demo Site B", "3", "B", "female", False, True, False, False, "out_of_service"),
    ("Demo Site B", "4", "A", "any", True, True, True, True, "available"),
    ("Demo Site B", "5", "A", "male", False, False, False, False, "available"),
]


def seed_demo_beds(session: Session) -> int:
    if session.exec(select(Bed)).first() is not None:
        return 0
    for site, room, label, sex, wc, ground, o2, iso, status in DEMO:
        bed = create_bed(session, BedCreate(site=site, room=room, label=label, sex_designation=sex,
                                            wheelchair_accessible=wc, ground_floor=ground, oxygen_ok=o2,
                                            isolation_ok=iso, notes="demo data"))
        bed.status = status
        session.add(bed)
    session.commit()
    return len(DEMO)
```

- [ ] **Step 5: Run tests**

Run: `.venv/Scripts/python -m pytest tests/test_beds_import.py tests/test_beds_service.py -v`
Expected: all passed

- [ ] **Step 6: Commit**

```bash
git add app/beds/importer.py app/beds/seed.py tests/test_beds_import.py
git commit -m "feat(beds): CSV/XLSX import with header mapping and invented demo seed"
```

---

### Task 11 (WS3): Bed API routes

**Files:**
- Create: `app/api/beds.py`, `tests/test_api_beds.py`, `docs/modules/bed-management.md`

**Interfaces:**
- Consumes: `app.db.get_session`, `app.beds.service.*`, `app.beds.importer.import_beds`.
- Produces: `router: APIRouter` (prefix `/api`). `GET /beds`, `GET /beds/summary`, `POST /beds`, `PATCH /beds/{bed_id}`, `POST /beds/import`, `GET /assignments`, `POST /assignments/{assignment_id}/discharge`. Task 14 includes this router in `app.main`.

- [ ] **Step 1: Write the failing tests `tests/test_api_beds.py`**

```python
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel

import app.beds.models  # noqa: F401
from app.api.beds import router
from app.beds import service
from app.beds.seed import seed_demo_beds
from app.db import get_session, make_engine


@pytest.fixture
def client():
    engine = make_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        seed_demo_beds(s)

    def override():
        with Session(engine) as s:
            yield s

    api = FastAPI()
    api.include_router(router)
    api.dependency_overrides[get_session] = override
    c = TestClient(api)
    c.engine = engine
    return c


def test_list_and_filter(client):
    beds = client.get("/api/beds").json()
    assert len(beds) == 18
    occupied = client.get("/api/beds", params={"status": "occupied"}).json()
    assert all(b["status"] == "occupied" for b in occupied) and occupied


def test_summary(client):
    s = client.get("/api/beds/summary").json()
    assert s["total"] == 18 and s["occupied"] == 4 and s["out_of_service"] == 1


def test_create_patch(client):
    r = client.post("/api/beds", json={"site": "Demo Site C", "room": "9", "label": "A"})
    assert r.status_code == 201
    bed_id = r.json()["id"]
    r = client.patch(f"/api/beds/{bed_id}", json={"status": "out_of_service"})
    assert r.json()["status"] == "out_of_service"
    assert client.patch("/api/beds/nope", json={"notes": "x"}).status_code == 404


def test_import(client):
    csv = b"Site,Room,Bed\nDemo Site D,1,A\n"
    r = client.post("/api/beds/import", files={"file": ("beds.csv", csv, "text/csv")})
    assert r.json() == {"created": 1, "updated": 0, "errors": []}


def test_assignments_and_discharge(client):
    with Session(client.engine) as s:
        a = service.assign(s, "demo-site-a-102-a", "R1", client_label="R1")
        aid = a.id
    assert [x["id"] for x in client.get("/api/assignments").json()] == [aid]
    r = client.post(f"/api/assignments/{aid}/discharge")
    assert r.json()["status"] == "released"
    assert client.post("/api/assignments/9999/discharge").status_code == 404
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python -m pytest tests/test_api_beds.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.api.beds'`

- [ ] **Step 3: Write `app/api/beds.py`**

```python
"""Bed management HTTP routes."""
from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlmodel import Session

from app.beds import service
from app.beds.importer import import_beds
from app.db import get_session
from app.schemas import AssignmentView, BedCreate, BedImportReport, BedSummary, BedUpdate, BedView

router = APIRouter(prefix="/api", tags=["beds"])


@router.get("/beds", response_model=list[BedView])
def list_beds(site: str | None = None, status: str | None = None, session: Session = Depends(get_session)):
    return service.list_beds(session, site=site, status=status)


@router.get("/beds/summary", response_model=BedSummary)
def bed_summary(session: Session = Depends(get_session)):
    return service.summary(session)


@router.post("/beds", response_model=BedView, status_code=201)
def create_bed(data: BedCreate, session: Session = Depends(get_session)):
    return service.to_view(session, service.create_bed(session, data))


@router.patch("/beds/{bed_id}", response_model=BedView)
def update_bed(bed_id: str, data: BedUpdate, session: Session = Depends(get_session)):
    try:
        return service.to_view(session, service.update_bed(session, bed_id, data))
    except service.BedNotFound:
        raise HTTPException(404, f"Bed {bed_id} not found")


@router.post("/beds/import", response_model=BedImportReport)
async def import_bed_file(file: UploadFile, session: Session = Depends(get_session)):
    return import_beds(session, file.filename or "", await file.read())


@router.get("/assignments", response_model=list[AssignmentView])
def list_assignments(active: bool = True, session: Session = Depends(get_session)):
    return service.list_assignments(session, active_only=active)


@router.post("/assignments/{assignment_id}/discharge", response_model=AssignmentView)
def discharge(assignment_id: int, session: Session = Depends(get_session)):
    try:
        return AssignmentView(**service.discharge(session, assignment_id).model_dump())
    except service.BedNotFound:
        raise HTTPException(404, f"Assignment {assignment_id} not found")
```

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python -m pytest tests/test_api_beds.py -v`
Expected: all passed

- [ ] **Step 5: Write `docs/modules/bed-management.md`**

```markdown
# Bed management (`app/beds/`, `app/api/beds.py`)

**Purpose:** replace IHH's Excel bed sheet: beds and their attributes, availability, holds while a referral is
reviewed, assignments on approval, discharge.

**Model:** `Site` → `Bed` (sex designation male/female/any, wheelchair_accessible, ground_floor, oxygen_ok,
isolation_ok, stored status available/occupied/out_of_service) · `Hold` (referral_id, expires_at, default 4 h) ·
`Assignment` (referral_id, client_label, dates, active/released). **"held" is derived** from an active, unexpired hold.

**Rules:** matching filters by availability, sex compatibility (unknown sex → only "any" beds), wheelchair, oxygen,
isolation; it ranks the least specialized bed first so accessible/oxygen beds are kept for those who need them.
`assign` re-checks availability inside the transaction and raises `BedUnavailable` (→ HTTP 409).

**Import:** `POST /api/beds/import` takes .csv/.xlsx. Headers are matched case-insensitively (`DEFAULT_MAPPING` in
`importer.py`). Bed ID = explicit `id` column, else `slug(site-room-bed)`. Re-import updates.

**Pending:** IHH's real sheet layout. When it arrives, extend `DEFAULT_MAPPING` and the `Bed` fields only. The
`service.py` interface stays stable.

**Testing:** `pytest tests/test_beds_service.py tests/test_beds_import.py tests/test_api_beds.py`.
```

- [ ] **Step 6: Commit**

```bash
git add app/api/beds.py tests/test_api_beds.py docs/modules/bed-management.md
git commit -m "feat(beds): bed management API routes and module docs"
```

---

### Task 12 (WS4): Referral/audit tables, audit log, drafts, mock Kipu

**Files:**
- Create: `app/workflow/models.py`, `app/workflow/audit.py`, `app/workflow/drafts.py`, `app/workflow/kipu.py`, `tests/test_workflow_parts.py`

**Interfaces:**
- Consumes: `AuditEventView`, `Recommendation`, `RuleResult`, `Rule`, `CaseSummary`, `BedOption`.
- Produces: tables `Referral`, `AuditEvent`; `audit.record(session, action, actor="system", referral_id=None, **detail) -> AuditEvent`; `audit.list_events(session, referral_id=None) -> list[AuditEventView]`; `drafts.denial_draft(referral_id, recommendation, results, rules_by_id, coordinator, notes) -> str`; `drafts.info_request_draft(referral_id, recommendation, coordinator, notes) -> str`; `kipu.KipuAdmissionDraft`; `kipu.KipuClient` (Protocol: `create_admission(draft) -> str`); `kipu.MockKipuClient(outbox_dir: Path)`.

- [ ] **Step 1: Write `app/workflow/models.py`**

```python
"""Referral and audit tables. JSON columns hold schemas.AnalysisResult / CoordinatorDecisionRecord / Drafts."""
from datetime import datetime

from sqlmodel import Field, SQLModel


class Referral(SQLModel, table=True):
    id: str = Field(primary_key=True)
    filename: str
    pdf_path: str
    packet_hash: str | None = None
    status: str = "uploaded"
    progress: str | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime
    result_json: str | None = None
    decision_json: str | None = None
    drafts_json: str | None = None


class AuditEvent(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    ts: datetime
    actor: str
    referral_id: str | None = Field(default=None, index=True)
    action: str
    detail_json: str = "{}"
```

- [ ] **Step 2: Write the failing tests `tests/test_workflow_parts.py`**

```python
import json

import pytest
from sqlmodel import Session, SQLModel

import app.workflow.models  # noqa: F401
from app.db import make_engine
from app.workflow import audit
from app.workflow.drafts import denial_draft, info_request_draft
from app.workflow.kipu import KipuAdmissionDraft, MockKipuClient
from app.schemas import HospitalQuestion, Recommendation
from tests.factories import make_bed_option, make_result, make_rule


@pytest.fixture
def session():
    engine = make_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def rec(decision="deny", questions=()):
    return Recommendation(decision=decision, headline="h", top_reasons=[], review_reasons=[],
                          hospital_questions=list(questions), tree=[])


def test_audit_record_and_list(session):
    audit.record(session, "uploaded", referral_id="R1", filename="a.pdf")
    audit.record(session, "other", referral_id="R2")
    events = audit.list_events(session, referral_id="R1")
    assert len(events) == 1 and events[0].detail == {"filename": "a.pdf"} and events[0].actor == "system"
    assert len(audit.list_events(session)) == 2


def test_denial_draft_cites_failed_rules_and_is_marked_draft():
    rules = {"MH-2": make_rule("MH-2", "exclude"), "MOB-3": make_rule("MOB-3")}
    results = [make_result("MH-2", "fail"), make_result("MOB-3", "pass")]
    text = denial_draft("R1", rec(), results, rules, "Coordinator A", "Call back if plan changes.")
    assert text.startswith("DRAFT")
    assert "MH-2" in text and "MOB-3" not in text
    assert "Coordinator A" in text and "Call back if plan changes." in text


def test_info_request_draft_lists_questions():
    r = rec("needs_review", [HospitalQuestion(rule_id="INF-1", question="Can you send the MRSA swab result?")])
    text = info_request_draft("R1", r, "Coordinator A", "")
    assert text.startswith("DRAFT") and "Can you send the MRSA swab result?" in text and "INF-1" in text


def test_mock_kipu_writes_outbox(tmp_path):
    client = MockKipuClient(tmp_path)
    draft = KipuAdmissionDraft(referral_id="R1", bed=make_bed_option(), admitting_diagnosis="cellulitis",
                               summary="Adult man...", ai_recommendation="approve", rule_outcomes={"pass": 3},
                               approved_by="Coordinator A", approved_at="2026-10-17T12:00:00")
    ref = client.create_admission(draft)
    assert ref == "MOCK-KIPU-R1"
    data = json.loads((tmp_path / "kipu" / "R1.json").read_text("utf-8"))
    assert data["mock"] is True and data["bed"]["bed_id"] == "SA-101-A"
```

- [ ] **Step 3: Run to verify failure**

Run: `.venv/Scripts/python -m pytest tests/test_workflow_parts.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 4: Write `app/workflow/audit.py`**

```python
"""Append-only audit log. Records AI recommendations, coordinator decisions and overrides (our QC trail)."""
import json
from datetime import datetime, timezone

from sqlmodel import Session, select

from app.schemas import AuditEventView
from app.workflow.models import AuditEvent


def record(session: Session, action: str, actor: str = "system", referral_id: str | None = None,
           **detail) -> AuditEvent:
    ev = AuditEvent(ts=datetime.now(timezone.utc).replace(tzinfo=None), actor=actor, referral_id=referral_id,
                    action=action, detail_json=json.dumps(detail, default=str))
    session.add(ev)
    session.commit()
    session.refresh(ev)
    return ev


def list_events(session: Session, referral_id: str | None = None) -> list[AuditEventView]:
    q = select(AuditEvent).order_by(AuditEvent.id)
    if referral_id:
        q = q.where(AuditEvent.referral_id == referral_id)
    return [AuditEventView(id=e.id, ts=e.ts, actor=e.actor, referral_id=e.referral_id, action=e.action,
                           detail=json.loads(e.detail_json)) for e in session.exec(q).all()]
```

- [ ] **Step 5: Write `app/workflow/drafts.py`**

```python
"""Deterministic message drafts for the coordinator to edit and send themselves. Never sent by this app."""
from app.schemas import Recommendation, Rule, RuleResult

HEADER = "DRAFT: not sent. Review and edit before sending.\n\n"


def denial_draft(referral_id: str, recommendation: Recommendation, results: list[RuleResult],
                 rules_by_id: dict[str, Rule], coordinator: str, notes: str) -> str:
    failed = [r for r in results if r.status == "fail" and r.rule_id in rules_by_id
              and rules_by_id[r.rule_id].kind in ("must", "exclude")]
    lines = [f"- {r.reasoning.strip()} (IHH admission criteria {r.rule_id})" for r in failed] or \
            ["- See coordinator notes."]
    body = (f"Re: Recuperative care referral {referral_id}\n\n"
            "Thank you for referring this patient to Illumination Health + Home. After reviewing the referral "
            "packet against our recuperative care admission criteria, we are not able to accept this referral "
            "at this time, for the following reason(s):\n\n" + "\n".join(lines) + "\n")
    if notes:
        body += f"\nAdditional notes: {notes}\n"
    body += ("\nIf the patient's situation changes, please feel free to send a new referral.\n\n"
             f"{coordinator}\nReferral Coordinator, Illumination Health + Home")
    return HEADER + body


def info_request_draft(referral_id: str, recommendation: Recommendation, coordinator: str, notes: str) -> str:
    questions = [f"{i}. {q.question} ({q.rule_id})" for i, q in enumerate(recommendation.hospital_questions, 1)] or \
                ["1. See notes below."]
    body = (f"Re: Recuperative care referral {referral_id}: information needed\n\n"
            "Thank you for the referral. To finish our review we need the following:\n\n"
            + "\n".join(questions) + "\n")
    if notes:
        body += f"\nNotes: {notes}\n"
    body += f"\nThank you,\n{coordinator}\nReferral Coordinator, Illumination Health + Home"
    return HEADER + body
```

- [ ] **Step 6: Write `app/workflow/kipu.py`**

```python
"""Kipu EHR integration: MOCKED. Event rules forbid connecting to IHH systems.

The real Kipu API field mapping has NOT been done; the draft below carries only what we know, under our own names.
Swap MockKipuClient for a real client that implements KipuClient after the event.
"""
from datetime import datetime
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel

from app.schemas import BedOption


class KipuAdmissionDraft(BaseModel):
    mock: bool = True
    note: str = "Prototype payload. Not sent to Kipu; field mapping to the real Kipu API is not done."
    referral_id: str
    bed: BedOption
    admitting_diagnosis: str | None
    summary: str
    ai_recommendation: str
    rule_outcomes: dict[str, int]
    approved_by: str
    approved_at: datetime


class KipuClient(Protocol):
    def create_admission(self, draft: KipuAdmissionDraft) -> str: ...


class MockKipuClient:
    def __init__(self, outbox_dir: Path):
        self.folder = outbox_dir / "kipu"

    def create_admission(self, draft: KipuAdmissionDraft) -> str:
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder / f"{draft.referral_id}.json").write_text(draft.model_dump_json(indent=2), "utf-8")
        return f"MOCK-KIPU-{draft.referral_id}"
```

- [ ] **Step 7: Run tests**

Run: `.venv/Scripts/python -m pytest tests/test_workflow_parts.py -v`
Expected: all passed

- [ ] **Step 8: Commit**

```bash
git add app/workflow/models.py app/workflow/audit.py app/workflow/drafts.py app/workflow/kipu.py tests/test_workflow_parts.py
git commit -m "feat(workflow): referral/audit tables, audit log, message drafts, mock Kipu outbox"
```

---

### Task 13 (WS4): Pipeline orchestration + coordinator decisions

**Files:**
- Create: `app/workflow/pipeline.py`, `app/workflow/decisions.py`, `tests/test_pipeline.py`, `tests/test_decisions.py`

**Interfaces:**
- Consumes: `transcribe_packet` (Task 4), `evaluate_packet`, `PROMPT_VERSION` (Task 8), `verify_results`, `verify_summary` (Task 6), `decide` (Task 7), `load_criteria` (Task 5), `beds.service` (Task 9), `audit`, `drafts`, `kipu` (Task 12), `StructuredLLM` (Task 1). **If WS1–3 are not merged yet, the tests below still run**: they inject fakes for `transcribe` and `evaluate` and use the real `decide`, `verify` and beds once merged. Run them after merging WS2/WS3.
- Produces: `class Pipeline(llm, criteria, engine_getter=get_engine, settings=None, transcribe=transcribe_packet, evaluate=evaluate_packet)` with `async run(referral_id: str) -> None`; `get_pipeline() -> Pipeline` (FastAPI dependency, cached); `mark_interrupted(session) -> int`; `class DecisionError(Exception)` with `.status_code`; `apply_decision(session, referral_id, decision: CoordinatorDecision, criteria, kipu: KipuClient) -> Referral`; `get_kipu() -> KipuClient` (dependency).

- [ ] **Step 1: Write the failing tests `tests/test_pipeline.py`**

```python
from datetime import datetime

import pytest
from sqlmodel import Session, SQLModel

import app.beds.models  # noqa: F401
import app.workflow.models  # noqa: F401
from app.beds.models import Bed, Site
from app.db import make_engine
from app.rules.criteria import load_criteria
from app.schemas import AnalysisResult, RuleResult
from app.workflow.models import Referral
from app.workflow.pipeline import Pipeline, mark_interrupted
from tests.factories import make_packet, make_summary


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    eng = make_engine("sqlite://")
    SQLModel.metadata.create_all(eng)
    with Session(eng) as s:
        s.add(Site(id="sa", name="Demo Site A"))
        s.add(Bed(id="M1", site_id="sa", room="101", label="A", sex_designation="male"))
        now = datetime(2026, 10, 17)
        for rid in ("R1", "R2"):
            s.add(Referral(id=rid, filename=f"{rid}.pdf", pdf_path=str(tmp_path / f"{rid}.pdf"),
                           created_at=now, updated_at=now))
        s.commit()
    return eng


async def fake_transcribe(llm, pdf_path, cache_root, concurrency=8, on_progress=None):
    if on_progress:
        on_progress(3, 3)
    return make_packet()


def all_pass_evaluate(results_status="pass"):
    async def evaluate(llm, packet, criteria, concurrency=8):
        results = [RuleResult(rule_id=r.id, status="not_applicable", reasoning="n/a", confidence="high")
                   for r in criteria.rules]
        for r in results:
            if r.rule_id in ("GEN-1",):
                r.status, r.evidence = results_status, []
        return make_summary(), results, {"summary": 0.1, "rules": 0.2}
    return evaluate


def pipeline(engine, evaluate=None, transcribe=fake_transcribe):
    return Pipeline(llm=object(), criteria=load_criteria(), engine_getter=lambda: engine,
                    transcribe=transcribe, evaluate=evaluate or all_pass_evaluate())


async def test_run_success_stores_result_and_holds_bed(engine):
    await pipeline(engine).run("R1")
    with Session(engine) as s:
        ref = s.get(Referral, "R1")
        assert ref.status == "ready_for_review" and ref.packet_hash == "testhash"
        result = AnalysisResult.model_validate_json(ref.result_json)
        assert result.recommendation.held_bed.bed_id == "M1"
        from app.beds.service import held_bed_id
        assert held_bed_id(s, "R1") == "M1"


async def test_second_referral_cannot_take_held_bed(engine):
    await pipeline(engine).run("R1")
    await pipeline(engine).run("R2")
    with Session(engine) as s:
        r2 = AnalysisResult.model_validate_json(s.get(Referral, "R2").result_json)
        assert r2.recommendation.held_bed is None
        assert any(r.kind == "no_bed" for r in r2.recommendation.review_reasons)


async def test_failure_marks_failed_with_message(engine):
    async def boom(*a, **k):
        raise RuntimeError("vision call failed")
    await pipeline(engine, transcribe=boom).run("R1")
    with Session(engine) as s:
        ref = s.get(Referral, "R1")
        assert ref.status == "failed" and "vision call failed" in ref.error


def test_mark_interrupted(engine):
    with Session(engine) as s:
        ref = s.get(Referral, "R1")
        ref.status = "transcribing"
        s.add(ref)
        s.commit()
        assert mark_interrupted(s) == 1
        ref = s.get(Referral, "R1")
        assert ref.status == "failed" and "interrupted" in ref.error
```

Note: `all_pass_evaluate` marks GEN-1 `pass` without evidence. `decide` treats GEN as passing on status alone, and every other rule `not_applicable` → approve.

- [ ] **Step 2: Write the failing tests `tests/test_decisions.py`**

```python
import json
from datetime import datetime, timedelta

import pytest
from sqlmodel import Session, SQLModel, select

import app.beds.models  # noqa: F401
import app.workflow.models  # noqa: F401
from app.beds import service
from app.beds.models import Bed, Hold, Site
from app.db import make_engine
from app.rules.criteria import load_criteria
from app.schemas import CoordinatorDecision, Drafts
from app.workflow.decisions import DecisionError, apply_decision
from app.workflow.kipu import MockKipuClient
from app.workflow.models import Referral
from tests.factories import make_analysis, make_bed_option


@pytest.fixture
def setup(tmp_path):
    eng = make_engine("sqlite://")
    SQLModel.metadata.create_all(eng)
    s = Session(eng)
    s.add(Site(id="sa", name="Demo Site A"))
    s.add_all([Bed(id="M1", site_id="sa", room="101", label="A", sex_designation="male"),
               Bed(id="M2", site_id="sa", room="102", label="A", sex_designation="male")])
    now = datetime(2026, 10, 17)
    analysis = make_analysis("approve", held_bed=make_bed_option("M1"))
    s.add(Referral(id="R1", filename="a.pdf", pdf_path="x", status="ready_for_review", created_at=now,
                   updated_at=now, result_json=analysis.model_dump_json()))
    s.commit()
    service.hold(s, "M1", "R1", ttl_hours=4)
    return s, MockKipuClient(tmp_path)


def approve(**kw):
    return CoordinatorDecision(decision="approve", coordinator="Coordinator A", **kw)


def test_approve_assigns_held_bed_and_writes_kipu(setup, tmp_path):
    s, kipu = setup
    ref = apply_decision(s, "R1", approve(), load_criteria(), kipu)
    assert ref.status == "approved"
    assert s.get(Bed, "M1").status == "occupied"
    drafts = Drafts.model_validate_json(ref.drafts_json)
    assert drafts.kipu_reference == "MOCK-KIPU-R1" and (tmp_path / "kipu" / "R1.json").exists()
    assert json.loads(ref.decision_json)["agreed_with_ai"] is True


def test_double_submit_is_rejected_without_second_assignment(setup):
    s, kipu = setup
    apply_decision(s, "R1", approve(), load_criteria(), kipu)
    with pytest.raises(DecisionError) as e:
        apply_decision(s, "R1", approve(bed_id="M2"), load_criteria(), kipu)
    assert e.value.status_code == 409
    assert s.get(Bed, "M2").status == "available"


def test_expired_hold_taken_by_other_referral_gives_409_and_keeps_status(setup):
    s, kipu = setup
    h = s.exec(select(Hold)).first()
    h.expires_at = service.utcnow() - timedelta(minutes=1)
    s.add(h)
    s.commit()
    service.hold(s, "M1", "R9", ttl_hours=4)
    with pytest.raises(DecisionError) as e:
        apply_decision(s, "R1", approve(), load_criteria(), kipu)
    assert e.value.status_code == 409 and "no longer available" in str(e.value)
    assert s.get(Referral, "R1").status == "ready_for_review"


def test_approve_other_bed_by_id(setup):
    s, kipu = setup
    apply_decision(s, "R1", approve(bed_id="M2"), load_criteria(), kipu)
    assert s.get(Bed, "M2").status == "occupied"


def test_deny_releases_hold_and_drafts_denial_and_records_disagreement(setup):
    s, kipu = setup
    ref = apply_decision(s, "R1", CoordinatorDecision(decision="deny", coordinator="Coordinator A"),
                         load_criteria(), kipu)
    assert ref.status == "denied" and service.held_bed_id(s, "R1") is None
    assert Drafts.model_validate_json(ref.drafts_json).denial.startswith("DRAFT")
    assert json.loads(ref.decision_json)["agreed_with_ai"] is False


def test_request_info_keeps_hold(setup):
    s, kipu = setup
    ref = apply_decision(s, "R1", CoordinatorDecision(decision="request_info", coordinator="C"),
                         load_criteria(), kipu)
    assert ref.status == "info_requested" and service.held_bed_id(s, "R1") == "M1"
    assert Drafts.model_validate_json(ref.drafts_json).info_request.startswith("DRAFT")


def test_unknown_referral_404(setup):
    s, kipu = setup
    with pytest.raises(DecisionError) as e:
        apply_decision(s, "NOPE", approve(), load_criteria(), kipu)
    assert e.value.status_code == 404
```

- [ ] **Step 3: Run to verify failure**

Run: `.venv/Scripts/python -m pytest tests/test_pipeline.py tests/test_decisions.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.workflow.pipeline'`

- [ ] **Step 4: Write `app/workflow/pipeline.py`**

```python
"""Orchestrates one referral: transcribe -> evaluate -> verify -> decide -> hold bed. Runs as a background task."""
import logging
import time
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from sqlmodel import Session, select

from app.beds import service as beds
from app.config import Settings, get_settings
from app.db import get_engine
from app.ingest.ocr import transcribe_packet
from app.llm import StructuredLLM
from app.rules.criteria import Criteria, load_criteria
from app.rules.evaluate import evaluate_packet
from app.rules.prompts import PROMPT_VERSION
from app.rules.tree import decide
from app.rules.verify import verify_results, verify_summary
from app.schemas import AnalysisResult
from app.workflow import audit
from app.workflow.models import Referral

log = logging.getLogger(__name__)
IN_FLIGHT = ("uploaded", "transcribing", "analyzing")


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Pipeline:
    def __init__(self, llm, criteria: Criteria, engine_getter=get_engine, settings: Settings | None = None,
                 transcribe=transcribe_packet, evaluate=evaluate_packet):
        self.llm = llm
        self.criteria = criteria
        self.engine_getter = engine_getter
        self.settings = settings or get_settings()
        self.transcribe = transcribe
        self.evaluate = evaluate

    def _update(self, referral_id: str, **fields) -> None:
        with Session(self.engine_getter()) as s:
            ref = s.get(Referral, referral_id)
            for k, v in fields.items():
                setattr(ref, k, v)
            ref.updated_at = _now()
            s.add(ref)
            s.commit()

    async def run(self, referral_id: str) -> None:
        t0 = time.perf_counter()
        try:
            with Session(self.engine_getter()) as s:
                pdf_path = Path(s.get(Referral, referral_id).pdf_path)
            self._update(referral_id, status="transcribing", progress="Transcribing pages", error=None)
            packet = await self.transcribe(
                self.llm, pdf_path, self.settings.cache_dir, concurrency=self.settings.ocr_concurrency,
                on_progress=lambda d, n: self._update(referral_id, progress=f"Transcribing page {d} of {n}"))
            t1 = time.perf_counter()
            self._update(referral_id, status="analyzing", packet_hash=packet.packet_hash,
                         progress="Checking admission criteria")
            summary, results, timings = await self.evaluate(self.llm, packet, self.criteria,
                                                             concurrency=self.settings.rules_concurrency)
            results = verify_results(results, packet)
            summary = verify_summary(summary, packet)
            with Session(self.engine_getter()) as s:
                options = beds.find_matches(s, summary.bed_needs, for_referral=referral_id)
                rec = decide(self.criteria.rules, results, summary, options)
                if rec.held_bed:
                    try:
                        beds.hold(s, rec.held_bed.bed_id, referral_id, self.settings.hold_ttl_hours)
                    except beds.BedUnavailable:
                        log.warning("Bed %s taken between match and hold", rec.held_bed.bed_id)
                result = AnalysisResult(
                    prompt_version=PROMPT_VERSION, model=getattr(self.llm, "model", "unknown"), packet=packet,
                    summary=summary, rule_results=results, recommendation=rec,
                    timings={"transcribe": t1 - t0, **timings, "total": time.perf_counter() - t0})
                ref = s.get(Referral, referral_id)
                ref.result_json, ref.status, ref.progress, ref.updated_at = (
                    result.model_dump_json(), "ready_for_review", None, _now())
                s.add(ref)
                s.commit()
                audit.record(s, "analysis_complete", referral_id=referral_id, ai_recommendation=rec.decision,
                             held_bed=rec.held_bed.bed_id if rec.held_bed else None,
                             seconds=round(time.perf_counter() - t0, 1))
        except Exception as e:
            log.exception("Pipeline failed for %s", referral_id)
            self._update(referral_id, status="failed", progress=None, error=f"{type(e).__name__}: {e}")
            with Session(self.engine_getter()) as s:
                audit.record(s, "analysis_failed", referral_id=referral_id, error=str(e))


def mark_interrupted(session: Session) -> int:
    """On startup: anything still in flight was interrupted by a restart."""
    stuck = session.exec(select(Referral).where(Referral.status.in_(IN_FLIGHT))).all()
    for ref in stuck:
        ref.status, ref.progress, ref.error, ref.updated_at = (
            "failed", None, "Processing was interrupted (server restart). Use retry.", _now())
        session.add(ref)
    session.commit()
    return len(stuck)


@lru_cache(maxsize=1)
def get_pipeline() -> Pipeline:
    return Pipeline(llm=StructuredLLM(), criteria=load_criteria())
```

- [ ] **Step 5: Write `app/workflow/decisions.py`**

```python
"""Apply a coordinator's final decision: transactional bed assignment, drafts, mock Kipu, audit."""
from collections import Counter
from datetime import datetime, timezone

from sqlmodel import Session

from app.beds import service as beds
from app.config import get_settings
from app.rules.criteria import Criteria
from app.schemas import AnalysisResult, CoordinatorDecision, CoordinatorDecisionRecord, Drafts
from app.workflow import audit
from app.workflow.drafts import denial_draft, info_request_draft
from app.workflow.kipu import KipuAdmissionDraft, KipuClient, MockKipuClient
from app.workflow.models import Referral

DECIDABLE = ("ready_for_review", "info_requested")
AGREES = {"approve": "approve", "deny": "deny", "request_info": "needs_review"}


class DecisionError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


def get_kipu() -> KipuClient:
    return MockKipuClient(get_settings().outbox_dir)


def apply_decision(session: Session, referral_id: str, decision: CoordinatorDecision, criteria: Criteria,
                   kipu: KipuClient) -> Referral:
    ref = session.get(Referral, referral_id)
    if ref is None:
        raise DecisionError(404, f"Referral {referral_id} not found")
    if ref.status not in DECIDABLE or not ref.result_json:
        raise DecisionError(409, f"Referral {referral_id} cannot be decided in status '{ref.status}'")
    result = AnalysisResult.model_validate_json(ref.result_json)
    ai = result.recommendation.decision
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    drafts = Drafts.model_validate_json(ref.drafts_json) if ref.drafts_json else Drafts()

    if decision.decision == "approve":
        bed_id = decision.bed_id or beds.held_bed_id(session, referral_id)
        if bed_id is None:
            raise DecisionError(409, "The held bed is no longer available (hold expired or released). "
                                     "Choose a bed from bed-matches.")
        try:
            beds.assign(session, bed_id, referral_id, client_label=referral_id)
        except (beds.BedUnavailable, beds.BedNotFound) as e:
            raise DecisionError(409, f"Bed {bed_id} is no longer available. Choose another bed. ({e})")
        bed = beds.bed_option(session, session.get(beds.Bed, bed_id))
        draft = KipuAdmissionDraft(
            referral_id=referral_id, bed=bed, admitting_diagnosis=result.summary.admitting_diagnosis,
            summary=result.summary.one_liner, ai_recommendation=ai,
            rule_outcomes=dict(Counter(r.status for r in result.rule_results)),
            approved_by=decision.coordinator, approved_at=now)
        drafts.kipu_payload = draft.model_dump(mode="json")
        drafts.kipu_reference = kipu.create_admission(draft)
        ref.status = "approved"
    elif decision.decision == "deny":
        beds.release_hold(session, referral_id)
        drafts.denial = denial_draft(referral_id, result.recommendation, result.rule_results, criteria.by_id(),
                                     decision.coordinator, decision.notes)
        ref.status = "denied"
    else:
        drafts.info_request = info_request_draft(referral_id, result.recommendation, decision.coordinator,
                                                 decision.notes)
        ref.status = "info_requested"

    record = CoordinatorDecisionRecord(**decision.model_dump(), decided_at=now, ai_recommendation=ai,
                                       agreed_with_ai=AGREES[decision.decision] == ai)
    ref.decision_json, ref.drafts_json, ref.updated_at = record.model_dump_json(), drafts.model_dump_json(), now
    session.add(ref)
    session.commit()
    audit.record(session, "coordinator_decision", actor=decision.coordinator, referral_id=referral_id,
                 decision=decision.decision, ai_recommendation=ai, agreed_with_ai=record.agreed_with_ai,
                 overrides=[o.model_dump() for o in decision.rule_overrides], notes=decision.notes)
    session.refresh(ref)
    return ref
```

Note: `beds.Bed` must be importable from `app.beds.service` (it is: `from app.beds.models import Bed` at the top of service.py).

- [ ] **Step 6: Run tests**

Run: `.venv/Scripts/python -m pytest tests/test_pipeline.py tests/test_decisions.py -v`
Expected: all passed. A test about re-deciding an `info_requested` referral is not needed: `DECIDABLE` allows it on purpose, because the coordinator decides again after the hospital answers.

- [ ] **Step 7: Commit**

```bash
git add app/workflow/pipeline.py app/workflow/decisions.py tests/test_pipeline.py tests/test_decisions.py
git commit -m "feat(workflow): pipeline orchestration, interrupted-run recovery, transactional decisions"
```

---

### Task 14 (WS4): Referral + audit API, app entrypoint

**Files:**
- Create: `app/api/referrals.py`, `app/api/audit.py`, `app/main.py`, `tests/test_api_referrals.py`, `docs/modules/workflow.md`

**Interfaces:**
- Consumes: `get_pipeline`, `Pipeline.run`, `mark_interrupted`, `apply_decision`, `DecisionError`, `get_kipu`, `validate_pdf`, `InvalidPDF`, `render_page`, `CORRECTION`, `load_criteria`, `beds.service`, `audit`, `init_db`, `get_engine`, `seed_demo_beds`, `app.api.beds.router`.
- Produces: `app.main.app` (FastAPI) and the routes listed in `docs/api-contract.md`.

- [ ] **Step 1: Write the failing tests `tests/test_api_referrals.py`**

```python
import pymupdf
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.db import make_engine, set_engine
from app.workflow.models import Referral
from app.workflow.pipeline import get_pipeline
from tests.factories import make_analysis, make_bed_option


class FakePipeline:
    def __init__(self, engine):
        self.engine = engine

    async def run(self, referral_id):
        with Session(self.engine) as s:
            from app.beds import service
            service.hold(s, "demo-site-a-102-a", referral_id, 4)
            ref = s.get(Referral, referral_id)
            ref.status = "ready_for_review"
            ref.packet_hash = "testhash"
            ref.result_json = make_analysis("approve", make_bed_option("demo-site-a-102-a")).model_dump_json()
            s.add(ref)
            s.commit()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    engine = make_engine("sqlite://")
    set_engine(engine)
    from app.main import app
    app.dependency_overrides[get_pipeline] = lambda: FakePipeline(engine)
    with TestClient(app) as c:  # runs lifespan: init_db + seed
        yield c
    app.dependency_overrides.clear()


def pdf_bytes() -> bytes:
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "invented test page")
    return doc.tobytes()


def upload(client):
    return client.post("/api/referrals", files={"file": ("packet.pdf", pdf_bytes(), "application/pdf")})


def test_health(client):
    assert client.get("/api/health").json()["api_key_configured"] is True


def test_upload_runs_pipeline_and_summary_view(client):
    r = upload(client)
    assert r.status_code == 202
    rid = r.json()["id"]
    view = client.get(f"/api/referrals/{rid}").json()
    assert view["status"] == "ready_for_review" and view["decision"] == "approve"
    assert view["recommendation"]["held_bed"]["bed_id"] == "demo-site-a-102-a"
    assert view["one_liner"].startswith("Adult man")
    assert [x["id"] for x in client.get("/api/referrals").json()] == [rid]


def test_details_join_rule_text(client):
    rid = upload(client).json()["id"]
    d = client.get(f"/api/referrals/{rid}/details").json()
    mob = next(r for r in d["rule_results"] if r["rule_id"] == "MOB-3")
    assert mob["kind"] == "must" and "100 ft" in mob["text"]
    assert d["pages"][0]["page"] == 1
    assert client.get(f"/api/referrals/{rid}/pages/2").json()["markdown"].startswith("PHYSICAL THERAPY")
    assert client.get(f"/api/referrals/{rid}/pages/99").status_code == 404


def test_page_image(client):
    rid = upload(client).json()["id"]
    r = client.get(f"/api/referrals/{rid}/pages/1/image")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"


def test_decision_flow_and_conflict(client):
    rid = upload(client).json()["id"]
    r = client.post(f"/api/referrals/{rid}/decision", json={"decision": "approve", "coordinator": "C"})
    assert r.status_code == 200 and r.json()["status"] == "approved"
    assert r.json()["coordinator_decision"]["agreed_with_ai"] is True
    again = client.post(f"/api/referrals/{rid}/decision", json={"decision": "approve", "coordinator": "C"})
    assert again.status_code == 409
    drafts = client.get(f"/api/referrals/{rid}/drafts").json()
    assert drafts["kipu_reference"] == f"MOCK-KIPU-{rid}"
    actions = [e["action"] for e in client.get("/api/audit", params={"referral_id": rid}).json()]
    assert actions[0] == "uploaded" and actions[-1] == "coordinator_decision"


def test_bed_matches(client):
    rid = upload(client).json()["id"]
    ids = [o["bed_id"] for o in client.get(f"/api/referrals/{rid}/bed-matches").json()]
    assert "demo-site-a-102-a" in ids  # own hold still counts as available


def test_non_pdf_upload_is_400_and_leaves_no_row(client):
    r = client.post("/api/referrals", files={"file": ("x.pdf", b"PK\x03\x04 docx", "application/pdf")})
    assert r.status_code == 400
    assert client.get("/api/referrals").json() == []
    r = client.post("/api/referrals", files={"file": ("empty.pdf", b"", "application/pdf")})
    assert r.status_code == 400


def test_upload_without_key_is_503(client, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    assert upload(client).status_code == 503


def test_retry_only_when_failed(client):
    rid = upload(client).json()["id"]
    assert client.post(f"/api/referrals/{rid}/retry").status_code == 409
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python -m pytest tests/test_api_referrals.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.main'`

- [ ] **Step 3: Write `app/api/referrals.py`**

```python
"""Referral intake HTTP routes."""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response, UploadFile
from sqlmodel import Session, select

from app.beds import service as beds
from app.config import get_settings
from app.db import get_session
from app.ingest.ocr import CORRECTION
from app.ingest.render import InvalidPDF, render_page, validate_pdf
from app.rules.criteria import load_criteria
from app.schemas import (
    AnalysisResult, BedOption, CoordinatorDecision, CoordinatorDecisionRecord, Drafts, PageInfo, PageMarkdown,
    ReferralDetails, ReferralListItem, ReferralView, RuleResultView,
)
from app.workflow import audit
from app.workflow.decisions import DecisionError, apply_decision, get_kipu
from app.workflow.models import Referral
from app.workflow.pipeline import Pipeline, get_pipeline

router = APIRouter(prefix="/api/referrals", tags=["referrals"])


def _get(session: Session, referral_id: str) -> Referral:
    ref = session.get(Referral, referral_id)
    if ref is None:
        raise HTTPException(404, f"Referral {referral_id} not found")
    return ref


def _result(ref: Referral) -> AnalysisResult:
    if not ref.result_json:
        raise HTTPException(409, f"Referral {ref.id} has not been analyzed yet (status '{ref.status}')")
    return AnalysisResult.model_validate_json(ref.result_json)


def _list_item(ref: Referral) -> dict:
    rec = AnalysisResult.model_validate_json(ref.result_json).recommendation if ref.result_json else None
    return dict(id=ref.id, filename=ref.filename, status=ref.status, created_at=ref.created_at,
                decision=rec.decision if rec else None, headline=rec.headline if rec else None)


def _view(ref: Referral) -> ReferralView:
    result = AnalysisResult.model_validate_json(ref.result_json) if ref.result_json else None
    return ReferralView(
        **_list_item(ref), progress=ref.progress, error=ref.error,
        one_liner=result.summary.one_liner if result else None,
        recommendation=result.recommendation if result else None,
        coordinator_decision=CoordinatorDecisionRecord.model_validate_json(ref.decision_json) if ref.decision_json else None)


@router.post("", response_model=ReferralView, status_code=202)
async def upload(file: UploadFile, background: BackgroundTasks, session: Session = Depends(get_session),
                 pipeline: Pipeline = Depends(get_pipeline)):
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise HTTPException(503, "ANTHROPIC_API_KEY is not configured on the server")
    referral_id = f"R-{uuid.uuid4().hex[:6].upper()}"
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    path = settings.uploads_dir / f"{referral_id}.pdf"
    path.write_bytes(await file.read())
    try:
        validate_pdf(path)
    except InvalidPDF as e:
        path.unlink(missing_ok=True)
        raise HTTPException(400, str(e))
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    ref = Referral(id=referral_id, filename=file.filename or "packet.pdf", pdf_path=str(path),
                   created_at=now, updated_at=now)
    session.add(ref)
    session.commit()
    audit.record(session, "uploaded", referral_id=referral_id, filename=ref.filename)
    background.add_task(pipeline.run, referral_id)
    session.refresh(ref)
    return _view(ref)


@router.get("", response_model=list[ReferralListItem])
def list_referrals(session: Session = Depends(get_session)):
    refs = session.exec(select(Referral).order_by(Referral.created_at.desc())).all()
    return [ReferralListItem(**_list_item(r)) for r in refs]


@router.get("/{referral_id}", response_model=ReferralView)
def get_referral(referral_id: str, session: Session = Depends(get_session)):
    return _view(_get(session, referral_id))


@router.get("/{referral_id}/details", response_model=ReferralDetails)
def get_details(referral_id: str, session: Session = Depends(get_session)):
    ref = _get(session, referral_id)
    result = _result(ref)
    rules = load_criteria().by_id()
    views = []
    for r in result.rule_results:
        rule = rules.get(r.rule_id)
        if rule is None:
            continue
        views.append(RuleResultView(**r.model_dump(), kind=rule.kind, section=rule.section, source=rule.source,
                                    text=rule.text, superseded_by=rule.superseded_by, human_review=rule.human_review))
    pages = [PageInfo(page=p.page, doc_type=p.doc_type, orientation=p.orientation, legibility=p.legibility)
             for p in result.packet.pages]
    return ReferralDetails(id=ref.id, status=ref.status, prompt_version=result.prompt_version, model=result.model,
                           summary=result.summary, rule_results=views, recommendation=result.recommendation,
                           pages=pages, timings=result.timings)


@router.get("/{referral_id}/pages/{page}", response_model=PageMarkdown)
def get_page(referral_id: str, page: int, session: Session = Depends(get_session)):
    result = _result(_get(session, referral_id))
    for p in result.packet.pages:
        if p.page == page:
            return p
    raise HTTPException(404, f"Page {page} not found")


@router.get("/{referral_id}/pages/{page}/image")
def get_page_image(referral_id: str, page: int, session: Session = Depends(get_session)):
    ref = _get(session, referral_id)
    rotate = 0
    if ref.result_json:
        pages = {p.page: p for p in AnalysisResult.model_validate_json(ref.result_json).packet.pages}
        if page not in pages:
            raise HTTPException(404, f"Page {page} not found")
        rotate = CORRECTION.get(pages[page].orientation, 0)
    data, media = render_page(get_settings().uploads_dir / f"{ref.id}.pdf", page, rotate=rotate)
    return Response(content=data, media_type=media)


@router.get("/{referral_id}/bed-matches", response_model=list[BedOption])
def bed_matches(referral_id: str, session: Session = Depends(get_session)):
    result = _result(_get(session, referral_id))
    return beds.find_matches(session, result.summary.bed_needs, limit=10, for_referral=referral_id)


@router.post("/{referral_id}/decision", response_model=ReferralView)
def decide(referral_id: str, decision: CoordinatorDecision, session: Session = Depends(get_session)):
    try:
        ref = apply_decision(session, referral_id, decision, load_criteria(), get_kipu())
    except DecisionError as e:
        raise HTTPException(e.status_code, str(e))
    return _view(ref)


@router.get("/{referral_id}/drafts", response_model=Drafts)
def get_drafts(referral_id: str, session: Session = Depends(get_session)):
    ref = _get(session, referral_id)
    return Drafts.model_validate_json(ref.drafts_json) if ref.drafts_json else Drafts()


@router.post("/{referral_id}/retry", response_model=ReferralView, status_code=202)
def retry(referral_id: str, background: BackgroundTasks, session: Session = Depends(get_session),
          pipeline: Pipeline = Depends(get_pipeline)):
    ref = _get(session, referral_id)
    if ref.status != "failed":
        raise HTTPException(409, f"Only failed referrals can be retried (status '{ref.status}')")
    ref.status, ref.error = "uploaded", None
    session.add(ref)
    session.commit()
    audit.record(session, "retry", referral_id=referral_id)
    background.add_task(pipeline.run, referral_id)
    session.refresh(ref)
    return _view(ref)
```

Note: `get_page_image` uses `uploads_dir/{id}.pdf`, which is the same path `upload` writes. This keeps the image endpoint working even before analysis finishes.

- [ ] **Step 4: Write `app/api/audit.py`**

```python
from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.db import get_session
from app.schemas import AuditEventView
from app.workflow import audit

router = APIRouter(prefix="/api", tags=["audit"])


@router.get("/audit", response_model=list[AuditEventView])
def list_audit(referral_id: str | None = None, session: Session = Depends(get_session)):
    return audit.list_events(session, referral_id=referral_id)
```

- [ ] **Step 5: Write `app/main.py`**

```python
"""FastAPI entrypoint: uvicorn app.main:app --reload"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import Session

from app.api import audit as audit_api
from app.api import beds as beds_api
from app.api import referrals as referrals_api
from app.beds.seed import seed_demo_beds
from app.config import get_settings
from app.db import get_engine, init_db
from app.workflow.pipeline import mark_interrupted

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    with Session(get_engine()) as s:
        seed_demo_beds(s)
        mark_interrupted(s)
    yield


app = FastAPI(title="IHH Referral Intake (prototype)", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(referrals_api.router)
app.include_router(beds_api.router)
app.include_router(audit_api.router)


@app.get("/api/health")
def health():
    s = get_settings()
    return {"ok": True, "model": s.model, "api_key_configured": bool(s.anthropic_api_key)}
```

- [ ] **Step 6: Run the full suite**

Run: `.venv/Scripts/python -m pytest -q`
Expected: all passed (requires WS1–WS3 merged).

- [ ] **Step 7: Write `docs/modules/workflow.md`**

```markdown
# Workflow + referral API (`app/workflow/`, `app/api/referrals.py`)

**Lifecycle:** `uploaded → transcribing → analyzing → ready_for_review → approved | denied | info_requested`, or
`failed` (error text shown; `POST /retry` re-runs and reuses the OCR cache). On startup, referrals left in flight are
marked failed ("interrupted").

**Pipeline** (`pipeline.py`, FastAPI BackgroundTask): transcribe → evaluate → verify → decide → hold the top bed
(4 h TTL). The full `AnalysisResult` is stored as JSON on the referral row.

**Decisions** (`decisions.py`): approve assigns the held bed (or `bed_id`) inside one transaction and writes the mock
Kipu payload to `data/outbox/kipu/{id}.json`; deny releases the hold and drafts a denial; request_info keeps the hold
and drafts the hospital questions. Every decision is audited with `ai_recommendation` and `agreed_with_ai`.
Double submits → 409.

**Drafts are never sent.** Kipu is mocked (event rule: no IHH systems).

**Testing:** `pytest tests/test_workflow_parts.py tests/test_pipeline.py tests/test_decisions.py tests/test_api_referrals.py`.
```

- [ ] **Step 7b: Run the server and smoke-test**

```bash
.venv/Scripts/uvicorn app.main:app --port 8000
# separately: curl http://localhost:8000/api/health ; curl http://localhost:8000/api/beds/summary
```
Expected: health JSON; summary shows 18 demo beds.

- [ ] **Step 8: Commit**

```bash
git add app/api/referrals.py app/api/audit.py app/main.py tests/test_api_referrals.py docs/modules/workflow.md
git commit -m "feat(api): referral intake routes, audit route, app entrypoint with startup recovery"
```

---

### Task 15 (WS5): Eval harness

**Files:**
- Create: `eval/run.py`, `tests/test_eval_score.py`

**Interfaces:**
- Consumes: `transcribe_packet`, `evaluate_packet`, `verify_results`, `verify_summary`, `decide`, `load_criteria`, `StructuredLLM`, `make_engine`-free (no DB), `BedOption`.
- Produces: `score(rows: list[dict], truth: dict[str, str]) -> str` (markdown); CLI `python -m eval.run --packets-dir DIR --decisions FILE [--only R01,R02] [--concurrency 2] [--out docs/eval-scorecard.md]`.

- [ ] **Step 1: Write the failing test `tests/test_eval_score.py`**

```python
from eval.run import outcome, score


def test_outcome_labels():
    assert outcome("approve", "approve") == "match"
    assert outcome("deny", "approve") == "FALSE APPROVE"
    assert outcome("approve", "deny") == "false deny"
    assert outcome("deny", "needs_review") == "review (IHH denied)"


def test_score_table_has_no_quotes_and_totals():
    rows = [
        {"packet_id": "R01", "decision": "deny", "deciding_rules": ["MOB-3"], "review_rules": [], "seconds": 90.0},
        {"packet_id": "R02", "decision": "deny", "deciding_rules": ["MH-2"], "review_rules": [], "seconds": 80.0},
        {"packet_id": "R03", "decision": "needs_review", "deciding_rules": [], "review_rules": ["INF-1"], "seconds": 70.0},
    ]
    md = score(rows, {"R01": "deny", "R02": "approve", "R03": "approve"})
    assert "| R01 | deny | deny | match | MOB-3 |" in md
    assert "False approvals: 0" in md and "False denials: 1" in md and "Needs review: 1" in md
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python -m pytest tests/test_eval_score.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'eval.run'`

- [ ] **Step 3: Write `eval/run.py`**

```python
"""Run the pipeline over the 11 packets and score it against IHH's decisions.

The decisions file is read only AFTER every packet has been processed, so it can never influence the review.
The scorecard holds packet IDs, decisions and rule IDs only: never packet text or quotes.

Usage:
  python -m eval.run --packets-dir ../claude-impact-lab-oc/challenges/1-referral-intake/packets \
                     --decisions ../claude-impact-lab-oc/challenges/1-referral-intake/eval/decisions.json
"""
import argparse
import asyncio
import json
import time
from pathlib import Path

from app.config import get_settings
from app.ingest.ocr import transcribe_packet
from app.llm import StructuredLLM
from app.rules.criteria import load_criteria
from app.rules.evaluate import evaluate_packet
from app.rules.prompts import PROMPT_VERSION
from app.rules.tree import decide
from app.rules.verify import verify_results, verify_summary
from app.schemas import AnalysisResult, BedOption

EVAL_BED = [BedOption(bed_id="EVAL", site="eval", room="-", label="-", match_notes=["eval placeholder"])]


def outcome(truth: str, ours: str) -> str:
    if ours == "needs_review":
        return f"review (IHH {'approved' if truth == 'approve' else 'denied'})"
    if ours == truth:
        return "match"
    return "FALSE APPROVE" if ours == "approve" else "false deny"


async def run_one(pdf: Path, llm, criteria, out_dir: Path) -> dict:
    settings = get_settings()
    t0 = time.perf_counter()
    packet = await transcribe_packet(llm, pdf, settings.cache_dir, concurrency=settings.ocr_concurrency)
    summary, results, timings = await evaluate_packet(llm, packet, criteria, concurrency=settings.rules_concurrency)
    results, summary = verify_results(results, packet), verify_summary(summary, packet)
    rec = decide(criteria.rules, results, summary, EVAL_BED)
    seconds = time.perf_counter() - t0
    result = AnalysisResult(prompt_version=PROMPT_VERSION, model=llm.model, packet=packet, summary=summary,
                            rule_results=results, recommendation=rec, timings={**timings, "total": seconds})
    (out_dir / f"{pdf.stem}.json").write_text(result.model_dump_json(indent=2), "utf-8")
    deciding = [i for s in rec.tree if s.node in ("exclusions", "must_rules", "general_eligibility")
                and s.outcome == "triggered" for i in s.rule_ids]
    review = sorted({i for r in rec.review_reasons for i in r.rule_ids})
    print(f"{pdf.stem}: {rec.decision} ({seconds:.0f}s)")
    return {"packet_id": pdf.stem, "decision": rec.decision, "deciding_rules": deciding,
            "review_rules": review, "seconds": round(seconds, 1)}


def score(rows: list[dict], truth: dict[str, str]) -> str:
    lines = [f"# Eval scorecard\n", f"Prompt version `{PROMPT_VERSION}`. Generated by `python -m eval.run`. "
             "Rule IDs only; no packet content.\n",
             "| Packet | IHH | Ours | Outcome | Deciding rules | Review items | Seconds |",
             "|---|---|---|---|---|---|---|"]
    counts = {"match": 0, "FALSE APPROVE": 0, "false deny": 0, "review": 0}
    for r in sorted(rows, key=lambda x: x["packet_id"]):
        t = truth.get(r["packet_id"], "?")
        o = outcome(t, r["decision"])
        counts["review" if o.startswith("review") else o] += 1
        lines.append(f"| {r['packet_id']} | {t} | {r['decision']} | {o} | {', '.join(r['deciding_rules']) or '-'} "
                     f"| {', '.join(r['review_rules'][:8]) or '-'} | {r['seconds']} |")
    lines += ["", f"Matches: {counts['match']} / {len(rows)}",
              f"False approvals: {counts['FALSE APPROVE']} (worst error: we approved, IHH denied)",
              f"False denials: {counts['false deny']}", f"Needs review: {counts['review']}"]
    return "\n".join(lines) + "\n"


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--packets-dir", required=True, type=Path)
    ap.add_argument("--decisions", required=True, type=Path)
    ap.add_argument("--only", default="")
    ap.add_argument("--concurrency", type=int, default=2)
    ap.add_argument("--out", type=Path, default=Path("docs/eval-scorecard.md"))
    args = ap.parse_args()
    pdfs = sorted(args.packets_dir.glob("R*.pdf"))
    if args.only:
        pdfs = [p for p in pdfs if p.stem in set(args.only.split(","))]
    out_dir = get_settings().data_dir / "eval"
    out_dir.mkdir(parents=True, exist_ok=True)
    llm, criteria = StructuredLLM(), load_criteria()
    sem = asyncio.Semaphore(args.concurrency)

    async def guarded(p: Path):
        async with sem:
            return await run_one(p, llm, criteria, out_dir)

    rows = await asyncio.gather(*(guarded(p) for p in pdfs))
    truth_data = json.loads(args.decisions.read_text("utf-8"))  # read only now, after every review is done
    truth = {p["packet_id"]: p["decision"] for p in truth_data["packets"]}
    md = score(rows, truth)
    args.out.write_text(md, "utf-8")
    print(md)


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python -m pytest tests/test_eval_score.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add eval/run.py tests/test_eval_score.py
git commit -m "feat(eval): batch eval harness and scorecard (rule IDs only)"
```

---

### Task 16 (WS5): Live run on the 11 packets + prompt tuning

**Files:**
- Modify: `app/rules/prompts.py` (tuning only; bump `PROMPT_VERSION`), `docs/modules/rules-engine.md` (tuning log)
- Create: `docs/eval-scorecard.md` (generated), `tests/test_live.py`

**Interfaces:**
- Consumes: everything. Produces: a measured scorecard and a tuned prompt.

- [ ] **Step 1: Write the opt-in live smoke test `tests/test_live.py`**

```python
import os

import pymupdf
import pytest

from app.ingest.ocr import transcribe_packet
from app.llm import StructuredLLM

pytestmark = pytest.mark.live


@pytest.mark.skipif(not os.getenv("ANTHROPIC_API_KEY"), reason="needs ANTHROPIC_API_KEY")
async def test_live_transcribes_invented_page(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "PHYSICAL THERAPY EVALUATION", fontsize=14)
    page.insert_text((72, 100), "Patient ambulated 150 ft with front wheeled walker.", fontsize=12)
    page.set_rotation(90)  # arrives sideways, like a real fax
    pdf = tmp_path / "invented.pdf"
    doc.save(pdf)
    packet = await transcribe_packet(StructuredLLM(), pdf, tmp_path / "cache")
    assert "150 ft" in packet.pages[0].markdown
```

- [ ] **Step 2: Set the key and run the live smoke test**

```bash
cp .env.example .env   # then the user fills ANTHROPIC_API_KEY in .env
.venv/Scripts/python -m pytest -m live -v
```
Expected: 1 passed. If it fails on `fallbacks`/`betas`, check the log line "Server-side fallbacks rejected". `StructuredLLM` already retries without them.

- [ ] **Step 3: Run the eval on 2 packets first (one approve, one deny by IHH's labels; don't look them up while reviewing output)**

```bash
.venv/Scripts/python -m eval.run --only R02,R05 \
  --packets-dir ../claude-impact-lab-oc/challenges/1-referral-intake/packets \
  --decisions ../claude-impact-lab-oc/challenges/1-referral-intake/eval/decisions.json
```
Expected: two lines with decisions and timings, and a scorecard printed. Open `data/eval/R02.json` and check: page doc types are sensible, quotes are verified, and there are no `"Not evaluated by the model"` results.

- [ ] **Step 4: Run all 11**

```bash
.venv/Scripts/python -m eval.run \
  --packets-dir ../claude-impact-lab-oc/challenges/1-referral-intake/packets \
  --decisions ../claude-impact-lab-oc/challenges/1-referral-intake/eval/decisions.json
```
Expected: `docs/eval-scorecard.md` written. Record matches, false approvals, false denials, needs-review count.

- [ ] **Step 5: Tune on the misses (max 2 rounds, ~20 min each)**

For each FALSE APPROVE or false deny, open `data/eval/Rxx.json`, find the rule results that drove it, and classify the miss:
OCR miss (the fact is absent from the page markdown), rule-semantics miss (wrong status for its kind), or tree issue.
Fix by adjusting **general** instructions in `RULES_TASK`/`EVAL_SYSTEM` (e.g. clarifying how to treat a kind). Never
hard-code packet facts or expected answers. Bump `PROMPT_VERSION`, re-run only the affected packets with `--only`,
then all 11 once. OCR is cached, so re-runs cost only the rule calls.

- [ ] **Step 6: Log findings in `docs/modules/rules-engine.md` under "Known limits / tuning log"** in rule-ID terms only (e.g. "MOB-3 often unknown when PT note is sideways; orientation retry fixed 2 of 3"). No packet quotes.

- [ ] **Step 7: Commit**

```bash
git add app/rules/prompts.py docs/modules/rules-engine.md docs/eval-scorecard.md tests/test_live.py
git status --short   # confirm no data/ or pdf
git commit -m "eval: live run on 11 packets, prompt tuning, scorecard"
```

---

### Task 17 (WS6): README, demo script, submission checks

**Files:**
- Create: `README.md`, `docs/demo-script.md`

- [ ] **Step 1: Write `README.md`** following the upstream template, with these sections filled in from facts: project name, one-sentence pitch ("For IHH referral coordinators: upload a faxed referral packet, get a cited recommendation and a held bed in minutes, and decide with one click."), team, members, challenge `1 Referral intake`, the problem (3 sentences from `docs/context/challenge-brief.md`), what we built, how Claude is used (OCR per page, rule-by-rule evaluation with quotes, deterministic verifier and tree, human decides), Try it, the real-vs-mocked table, where it gets things wrong (copy numbers from `docs/eval-scorecard.md`), next steps, data and privacy (template text verbatim), license.

Try-it block:
```bash
git clone <this repo> && cd referral-intake-hackathon
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt   # macOS/Linux: .venv/bin/pip
cp .env.example .env    # add your ANTHROPIC_API_KEY
.venv/Scripts/uvicorn app.main:app --port 8000
# open http://localhost:8000/docs ; upload a packet PDF via POST /api/referrals
```

Real vs mocked table rows: PDF transcription (working), rule evaluation with citations (working), quote verification (working), decision tree (working), bed management with demo beds (working; demo data is invented, real sheet import via CSV/XLSX), Kipu upload (mocked: writes JSON to `data/outbox/kipu/`), hospital messages (drafts only, never sent), frontend (teammate's Claude Design UI; status as of submission).

- [ ] **Step 2: Write `docs/demo-script.md`** (2 minutes): (1) problem in one line, with page count and the 2-hour promise; (2) upload a packet and show progress; (3) summary card with decision, held bed and 3 reasons; (4) click details, open one evidence quote, show the page image; (5) show a `needs_review` case with hospital questions; (6) approve: bed becomes occupied in `/api/beds/summary`, Kipu mock payload in drafts; (7) scorecard: X/11 match, 0 false approvals, honest misses.

- [ ] **Step 3: Submission checks**

```bash
git log --stat --all -- '*.pdf' '*.zip' 'data/*'      # must print nothing
git grep -n "sk-ant-" || echo "no keys"                # must print "no keys"
.venv/Scripts/python -m pytest -q                      # all pass
```

- [ ] **Step 4: Commit and push**

```bash
git add README.md docs/demo-script.md
git commit -m "docs: README per event template and demo script"
git push
```
