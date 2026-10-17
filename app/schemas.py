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
BedStatus = Literal["available", "held", "occupied", "needs_cleaning", "out_of_service"]
ProgramType = Literal["recuperative_care", "project_roomkey", "shelter", "other", "unknown"]
StaySource = Literal["referral", "census", "seed"]
SexDesignation = Literal["male", "female", "any"]
LessonKind = Literal["bed_preference", "rule_interpretation", "decision_precedent", "county_mapping"]
LessonStatus = Literal["proposed", "approved", "rejected", "retired"]


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
    county: str | None = Field(default=None, description="Always null: the system fills this after county review")


class CountySuggestion(BaseModel):
    """System-filled (never by the model): county used for bed matching."""
    county: str | None = None
    candidates: list[str] = []
    basis: str = ""
    confirmed: bool = False
    confirmed_by: str | None = None


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
    health_plan: str | None = Field(default=None, description="Health plan / payer named in the packet (e.g. 'CalOptima'), exactly as written; null if none")
    health_plan_evidence: list[Evidence] = Field(default=[], description="Verbatim quotes showing the health plan")
    location_clues: list[Evidence] = Field(default=[], description="Verbatim quotes naming a city or county where the patient lives or is hospitalized (not [REDACTED] text)")
    suggested_county: CountySuggestion | None = Field(default=None, description="Always null: filled by the system")


# ---------- Decision tree ----------
class ReviewReason(BaseModel):
    kind: Literal["missing_info", "judgment_call", "unverified_evidence", "conflict", "no_bed", "county_needed"]
    rule_ids: list[str]
    detail: str


class HospitalQuestion(BaseModel):
    rule_id: str
    question: str


class TreeStep(BaseModel):
    node: Literal["exclusions", "must_rules", "general_eligibility", "human_review", "missing_info", "unverified", "conflicts", "bed", "knowledge"]
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
    precedents: list["Precedent"] = []


class AnalysisResult(BaseModel):
    prompt_version: str
    model: str
    packet: PacketText
    summary: CaseSummary
    rule_results: list[RuleResult]
    recommendation: Recommendation
    timings: dict[str, float] = {}
    knowledge_used: list[int] = []
    profile: "CaseProfile | None" = None


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
    county: CountySuggestion | None = None


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


class StaySummary(BaseModel):
    assignment_id: int
    source: StaySource
    referral_id: str | None = None
    client_id: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    gender: str | None = None
    intake_date: date | None = None
    expected_exit_date: date | None = None
    payer: str | None = None


class StayDetail(StaySummary):
    status: Literal["active", "released"]
    exit_date: date | None = None
    case_manager: str | None = None
    notes: str = ""
    extra: dict[str, Any] = {}
    released_at: datetime | None = None


class BedView(BaseModel):
    id: str
    site: str
    site_id: str = ""
    county: str | None = None
    program_type: ProgramType = "unknown"
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
    stay: StaySummary | None = None


class BedDetail(BaseModel):
    bed: BedView
    current_stay: StayDetail | None = None
    history: list[StaySummary] = []


class FacilityView(BaseModel):
    id: str
    name: str
    county: str | None = None
    program_type: ProgramType = "unknown"
    address: str = ""
    phone: str = ""
    notes: str = ""
    active: bool = True
    total_beds: int = 0
    available_beds: int = 0


class FacilityUpdate(BaseModel):
    county: str | None = None
    program_type: ProgramType | None = None
    address: str | None = None
    phone: str | None = None
    notes: str | None = None
    active: bool | None = None


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
    status: Literal["available", "needs_cleaning", "out_of_service"] | None = None
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
    needs_cleaning: int = 0


class AssignmentView(BaseModel):
    id: int
    bed_id: str
    referral_id: str | None = None
    client_label: str
    start_date: date
    expected_end_date: date | None = None
    released_at: datetime | None = None
    status: Literal["active", "released"]


class CensusSheetReport(BaseModel):
    sheet: str
    facility_id: str | None = None
    beds_created: int = 0
    beds_updated: int = 0
    stays_created: int = 0
    stays_closed: int = 0
    skipped: list[str] = []
    conflicts: list[str] = []


class CensusImportReport(BaseModel):
    facilities_created: int = 0
    sheets: list[CensusSheetReport] = []
    skipped_sheets: list[str] = []


class CountyConfirm(BaseModel):
    county: str = Field(min_length=1)
    coordinator: str = Field(min_length=1)


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


# ---------- Knowledge core (learning from coordinator overrides) ----------
class CaseProfile(BaseModel):
    """Coarse, de-identified case features used to match lessons. Never holds quotes or names."""
    county: str | None = None
    payer: str | None = None
    sex: Sex = "unknown"
    wheelchair: bool | None = None
    oxygen: bool | None = None
    isolation: bool | None = None
    mobility_aid: str | None = None
    systems: list[str] = []          # body systems whose eligible rules passed, e.g. ["Integumentary"]
    flagged_rule_ids: list[str] = []  # rules that failed or were unknown


class Precedent(BaseModel):
    lesson_id: int
    similarity: float
    summary: str
    ai_decision: str
    coordinator_decision: str
    note: str = ""


class LessonView(BaseModel):
    id: int
    kind: LessonKind
    status: LessonStatus
    summary: str
    created_at: datetime
    created_by: str
    source_referral_id: str | None = None
    profile: CaseProfile
    payload: dict[str, Any] = {}
    note: str = ""
    needs_note: bool = False
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    times_applied: int = 0


class LessonReview(BaseModel):
    reviewer: str = Field(min_length=1)
    note: str | None = None


Recommendation.model_rebuild()
AnalysisResult.model_rebuild()
