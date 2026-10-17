"""Import beds from IHH's spreadsheet (CSV or XLSX). Header names are matched case-insensitively via DEFAULT_MAPPING."""
import csv
import io

import openpyxl
from sqlmodel import Session

from app.beds.models import Bed
from app.beds.service import _active_assignment, create_bed, slug
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
    if not name.endswith((".csv", ".xlsx")):
        raise ValueError(f"Unsupported file type: {filename} (use .csv or .xlsx)")
    try:
        return _read_rows(name, data)
    except Exception as e:
        raise ValueError(f"Could not read {filename}: {e}")


def _read_rows(name: str, data: bytes) -> list[dict[str, str]]:
    if name.endswith(".csv"):
        rows = list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))
    else:
        ws = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True).active
        values = list(ws.iter_rows(values_only=True))
        headers = [str(h or "").strip() for h in values[0]] if values else []
        rows = [{headers[i]: ("" if v is None else str(v)) for i, v in enumerate(r) if i < len(headers)}
                for r in values[1:]]
    # DictReader stores extra cells under the key None (as a list); ignore those
    return [{k.strip().lower(): (v or "").strip() for k, v in r.items() if isinstance(k, str)} for r in rows]


def _import_row(session: Session, n: int, row: dict[str, str], errors: list[str]) -> str:
    fields = dict(
        sex_designation=_sex(row.get("sex_designation", "")),
        wheelchair_accessible=_bool(row.get("wheelchair_accessible", "")),
        ground_floor=_bool(row.get("ground_floor", "")),
        oxygen_ok=_bool(row.get("oxygen_ok", "")),
        isolation_ok=_bool(row.get("isolation_ok", "")),
        notes=row.get("notes", ""),
    )
    has_status = bool(row.get("status"))
    bed_id = row.get("id") or slug(f"{row['site']}-{row['room']}-{row['label']}")
    existing = session.get(Bed, bed_id)
    if existing:
        for k, v in fields.items():
            setattr(existing, k, v)
        if has_status:
            if _active_assignment(session, bed_id):
                errors.append(f"row {n}: bed {bed_id} has an active assignment; status not changed")
            else:
                existing.status = _status(row["status"])
        session.add(existing)
        session.commit()
        return "updated"
    bed = create_bed(session, BedCreate(id=bed_id, site=row["site"], room=row["room"], label=row["label"], **fields))
    if has_status:
        bed.status = _status(row["status"])
        session.add(bed)
        session.commit()
    return "created"


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
        try:
            if _import_row(session, n, row, errors) == "created":
                created += 1
            else:
                updated += 1
        except Exception as e:  # one bad row must not abort the import
            session.rollback()
            errors.append(f"row {n}: {e}")
    return BedImportReport(created=created, updated=updated, errors=errors)