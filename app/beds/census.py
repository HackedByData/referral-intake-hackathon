"""IHH bed-census workbook parser and importer.

One sheet per facility; a sheet may hold several bed tables side by side. See
docs/superpowers/specs/2026-10-17-bms-census-county-design.md.
"""
import io
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import openpyxl
from sqlmodel import Session, select

from app.beds.models import Assignment, Bed, Site
from app.beds.service import slug, utcnow
from app.schemas import CensusImportReport, CensusSheetReport

IF_HEADER = re.compile(r"^\s*I[FE]\s*/?\s*(I[FE])?\s*#")
BLOCK_HEADER = re.compile(r"^(bed|room|rm)\b", re.I)
IF_VALUE = re.compile(r"^\s*I[FE]", re.I)
SUMMARY_HEADER = re.compile(r"^(current|intakes today|female beds|male beds|total)", re.I)
SUMMARY_LABEL = re.compile(r"^(total|female beds|male beds|current|intakes today)|^\d+\s*/\s*\d+$|^#", re.I)
SUMMARY_CELL = re.compile(r"^(female|male|total|current|reserve|intakes)\b", re.I)

FIELD_SYNONYMS = {
    "client_id": {"if#", "if #", "if/ie#", "if/ie #", "ie#", "ie #"},
    "first_name": {"first", "first name", "name"},
    "last_name": {"last", "last name", "last int."},
    "gender": {"gender"},
    "intake_date": {"intake date", "admission", "admit date", "intake"},
    "expected_exit_date": {"anticipated discharge date"},
    "exit_date": {"exit date", "exit"},
    "payer": {"contract", "insurance", "referring agency", "referrer"},
    "case_manager": {"case manager"},
    "notes": {"notes"},
}
SYNONYM_TO_FIELD = {syn: f for f, syns in FIELD_SYNONYMS.items() for syn in syns}

OPEN_WORDS = {"open"}
CLEANING_WORDS = {"needs cleaning"}
OUT_WORDS = {"closed", "do not use", "unavailable", "offline", "no bed", "xxx", "@sfs", "no light", "no tv", "sink"}
NON_CLIENT_WORDS = {"staff", "staff restroom", "sm office", "office", "nursing station", "donning room",
                    "doffing room", "med room", "supply"}
DATE_BLANKS = {"", "tbd", "tbh"}


@dataclass
class ParsedRow:
    label: str
    status: str
    bed_notes: str = ""
    wheelchair: bool = False
    sex_designation: str = "any"
    stay: dict | None = None


@dataclass
class ParsedSheet:
    sheet: str
    rows: list[ParsedRow] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def _text(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    return re.sub(r"\s+", " ", str(v)).strip()


def _norm_header(v: Any) -> str:
    return re.sub(r"\s+", " ", _text(v)).strip().rstrip(":?").strip().lower()


def _is_summary_header(raw: str) -> bool:
    return bool(SUMMARY_HEADER.match(raw.strip())) or (":" in raw and any(ch.isdigit() for ch in raw))


def _find_header_row(rows: list[tuple]) -> int | None:
    for i, row in enumerate(rows[:3]):
        if any(isinstance(c, str) and IF_HEADER.match(c) for c in row):
            return i
    return None


def _block_starts(header: list[str]) -> list[int]:
    is_if = [bool(IF_HEADER.match(h)) for h in header]
    starts = []
    for j, h in enumerate(header):
        if BLOCK_HEADER.match(h.strip()):
            starts.append(j)
        elif j + 1 < len(header) and is_if[j + 1] and not re.search(r"[A-Za-z]", h):
            starts.append(j)
    # A start only counts when its segment (up to the next candidate start) has an IF column or its own
    # name/intake columns. A dropped candidate (e.g. a "Bed" attribute column in Royal Pagoda) is simply not a
    # boundary, so its columns are absorbed into the previous block's segment.
    kept: list[int] = []
    bounds = starts + [len(header)]
    for k, s in enumerate(starts):
        seg = bounds[k], bounds[k + 1]
        fields = {SYNONYM_TO_FIELD.get(_norm_header(h)) for h in header[seg[0] + 1:seg[1]]}
        if any(is_if[seg[0]:seg[1]]) or fields & {"first_name", "last_name", "intake_date"}:
            kept.append(s)
    return kept


def _to_date(v: Any) -> tuple[date | None, bool]:
    """-> (date or None, raw_kept) where raw_kept means an unparseable value that belongs in extra."""
    if isinstance(v, datetime):
        return v.date(), False
    if isinstance(v, date):
        return v, False
    s = _text(v)
    if s.lower() in DATE_BLANKS:
        return None, False
    try:
        return datetime.strptime(s, "%m/%d/%Y").date(), False
    except ValueError:
        return None, True


def _classify(first: str, last: str, id_cell: str, label: str, has_dates: bool) -> tuple[str, str]:
    """-> (kind, note) with kind in occupied | available | needs_cleaning | out_of_service | skip."""
    for cell in (first, id_cell, last, label):
        w = cell.lower()
        if w in NON_CLIENT_WORDS:
            return "skip", cell
    for cell in (first, last):
        w = cell.lower()
        if w in OUT_WORDS:
            return "out_of_service", cell
    f = first.lower()
    if f in CLEANING_WORDS:
        return "needs_cleaning", ""
    if f in OPEN_WORDS:
        return "available", ""
    if IF_VALUE.match(id_cell) or first or last or has_dates:
        return "occupied", ""
    return "available", ""


def _parse_block(header: list[str], rows: list[tuple], start: int, end: int, header_row: int) -> tuple[list[ParsedRow], list[str]]:
    # field mapping for this block
    cols: dict[int, str] = {}
    extra_cols: dict[int, str] = {}
    seen: dict[str, int] = {}
    first_name_cols = [j for j in range(start + 1, end) if _norm_header(header[j]) == "first name"]
    payer_cols: list[int] = []
    for j in range(start + 1, end):
        raw = header[j]
        norm = _norm_header(raw)
        if not norm or _is_summary_header(_text(raw)):
            continue
        f = SYNONYM_TO_FIELD.get(norm)
        if f == "first_name" and len(first_name_cols) == 2 and j == first_name_cols[1]:
            f = "last_name"  # Buena Park RC: two "First Name:" headers
        if f == "payer":
            payer_cols.append(j)
            continue
        if f and f not in cols.values():
            cols[j] = f
        else:
            seen[norm] = seen.get(norm, 0) + 1
            extra_cols[j] = _text(raw) if seen[norm] == 1 else f"{_text(raw)} ({seen[norm]})"
    out: list[ParsedRow] = []
    skipped: list[str] = []
    field_col = {f: j for j, f in cols.items()}
    for r in rows[header_row + 1:]:
        cell = lambda j: r[j] if j < len(r) else None  # noqa: E731
        label = _text(cell(start))
        if not label or not re.search(r"[A-Za-z0-9]", label):
            continue
        if SUMMARY_LABEL.match(label):
            skipped.append(f"{label} (summary footer; rest of block ignored)")
            break
        get = lambda f: _text(cell(field_col[f])) if f in field_col else ""  # noqa: E731
        first, last, id_cell = get("first_name"), get("last_name"), get("client_id")
        if SUMMARY_CELL.match(id_cell) or SUMMARY_CELL.match(first):
            skipped.append(f"{label} (summary footer; rest of block ignored)")
            break
        if id_cell and not IF_VALUE.match(id_cell) and re.fullmatch(r"[A-Za-z]", id_cell):
            label, id_cell = f"{label}{id_cell.upper()}", ""  # bed letter lives in the IF# column (Royal Pagoda)
        has_dates = any(get(f) not in ("", "TBD", "TBH") for f in ("intake_date",) if f in field_col)
        kind, bed_notes = _classify(first, last, id_cell, label, has_dates)
        if kind == "skip":
            skipped.append(f"{label} (not a client bed: {bed_notes})")
            continue
        if kind != "out_of_service":
            bed_notes = ""
        up = label.upper()
        sex = "male" if re.match(r"^M\d", up) else "female" if re.match(r"^W\d", up) else "any"
        row = ParsedRow(label=label, status="available" if kind == "available" else kind, bed_notes=bed_notes,
                        wheelchair="ADA" in up, sex_designation=sex)
        if kind == "occupied":
            extra: dict[str, str] = {}
            dates: dict[str, date | None] = {}
            for f in ("intake_date", "expected_exit_date", "exit_date"):
                if f in field_col:
                    d, raw_kept = _to_date(cell(field_col[f]))
                    dates[f] = d
                    if raw_kept:
                        extra[header[field_col[f]].strip() if isinstance(header[field_col[f]], str) else f] = _text(cell(field_col[f]))
            payer = ""
            for j in payer_cols:
                v = _text(cell(j))
                if v and not payer:
                    payer = v
                elif v:
                    extra[_text(header[j])] = v
            for j, h in extra_cols.items():
                v = _text(cell(j))
                if v:
                    extra[h] = v
            row.stay = {
                "client_id": id_cell or None, "first_name": first or None, "last_name": last or None,
                "gender": get("gender") or None, "intake_date": dates.get("intake_date"),
                "expected_exit_date": dates.get("expected_exit_date"), "exit_date": dates.get("exit_date"),
                "payer": payer or None, "case_manager": get("case_manager") or None,
                "notes": get("notes"), "extra": extra,
            }
        out.append(row)
    return out, skipped


def parse_workbook(data: bytes) -> tuple[list[ParsedSheet], list[str]]:
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=False)
    sheets: list[ParsedSheet] = []
    skipped_sheets: list[str] = []
    for ws in wb.worksheets:
        rows = [tuple(r) for r in ws.iter_rows(values_only=True)]
        hi = _find_header_row(rows)
        if hi is None:
            skipped_sheets.append(f"{ws.title}: no header row with an IF# column in rows 1-3")
            continue
        header = [c if isinstance(c, str) else ("" if c is None else str(c)) for c in rows[hi]]
        starts = _block_starts(header)
        if not starts:
            skipped_sheets.append(f"{ws.title}: no bed/room column found")
            continue
        sheet = ParsedSheet(sheet=ws.title)
        bounds = starts + [len(header)]
        for k, s in enumerate(starts):
            parsed, skipped = _parse_block(header, rows, s, bounds[k + 1], hi)
            sheet.rows.extend(parsed)
            sheet.skipped.extend(skipped)
        if not sheet.rows:
            skipped_sheets.append(f"{ws.title}: no rows with a bed label")
            continue
        sheets.append(sheet)
    return sheets, skipped_sheets


# ---------- import ----------

def _program_from_name(name: str) -> str:
    if re.search(r"rc\b|recup", name, re.I):
        return "recuperative_care"
    if re.search(r"prk", name, re.I):
        return "project_roomkey"
    if re.search(r"shelter", name, re.I):
        return "shelter"
    return "unknown"


def _same_stay(a: Assignment, stay: dict) -> bool:
    n = lambda v: (v or "").strip().lower()  # noqa: E731
    return n(a.client_id) == n(stay["client_id"]) and n(a.first_name) == n(stay["first_name"])


def _fill_stay(a: Assignment, stay: dict) -> None:
    a.client_id, a.first_name, a.last_name = stay["client_id"], stay["first_name"], stay["last_name"]
    a.gender, a.payer, a.case_manager = stay["gender"], stay["payer"], stay["case_manager"]
    a.intake_date, a.exit_date, a.expected_end_date = stay["intake_date"], stay["exit_date"], stay["expected_exit_date"]
    a.stay_notes = stay["notes"] or ""
    a.extra_json = json.dumps(stay["extra"], ensure_ascii=False)
    a.client_label = " ".join(p for p in (stay["first_name"], stay["last_name"]) if p) or stay["client_id"] or "Occupant"


def _upsert_row(session: Session, site: Site, row: ParsedRow, rep: CensusSheetReport, seen: set[str]) -> None:
    bed_id = slug(f"{site.id}-{row.label}")
    if bed_id in seen:
        rep.skipped.append(f"{row.label} (duplicate bed label in sheet)")
        return
    seen.add(bed_id)
    bed = session.get(Bed, bed_id)
    if bed is None:
        bed = Bed(id=bed_id, site_id=site.id, room=row.label, label=row.label)
        rep.beds_created += 1
    else:
        rep.beds_updated += 1
    bed.room = bed.label = row.label
    if row.wheelchair:
        bed.wheelchair_accessible = True
    if row.sex_designation != "any":
        bed.sex_designation = row.sex_designation
    if row.bed_notes:
        bed.notes = row.bed_notes
    session.add(bed)
    session.flush()
    active = session.exec(select(Assignment).where(Assignment.bed_id == bed_id, Assignment.status == "active")).all()
    if any(a.source == "referral" for a in active):
        if row.stay is not None:
            rep.conflicts.append(f"{row.label}: sheet shows an occupant but an approved referral holds the bed; left unchanged")
        return
    bed.status = row.status
    session.add(bed)
    census_stays = [a for a in active if a.source in ("census", "seed")]  # snapshot-replaceable
    keep = None
    for a in census_stays:
        if row.stay is not None and keep is None and _same_stay(a, row.stay):
            keep = a
        else:
            a.status, a.released_at = "released", utcnow()
            session.add(a)
            rep.stays_closed += 1
    if row.stay is not None:
        if keep is None:
            keep = Assignment(bed_id=bed_id, client_label="", start_date=row.stay["intake_date"] or utcnow().date(),
                              source="census")
            rep.stays_created += 1
        _fill_stay(keep, row.stay)
        session.add(keep)


def import_census(session: Session, sheets: list[ParsedSheet], program_hint: bool = True) -> CensusImportReport:
    report = CensusImportReport()
    for sh in sheets:
        site_id = slug(sh.sheet)
        rep = CensusSheetReport(sheet=sh.sheet, facility_id=site_id, skipped=list(sh.skipped))
        site = session.get(Site, site_id)
        if site is None:
            site = Site(id=site_id, name=sh.sheet,
                        program_type=_program_from_name(sh.sheet) if program_hint else "unknown")
            session.add(site)
            session.flush()
            report.facilities_created += 1
        seen: set[str] = set()
        for row in sh.rows:
            _upsert_row(session, site, row, rep, seen)
        session.flush()
        report.sheets.append(rep)
    session.commit()  # one commit: the whole workbook imports or nothing does
    return report
