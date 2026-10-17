"""INVENTED demo beds so the prototype works before IHH's real sheet is imported."""
import json
from datetime import date, datetime, timezone
from pathlib import Path

from sqlmodel import Session, select

from app.beds.models import Assignment, Bed, Site
from app.beds.service import create_bed, slug
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
    # Placeholder facility attributes so matching works in the demo (invented).
    for site_id, county in (("demo-site-a", "Orange"), ("demo-site-b", "Los Angeles")):
        site = session.get(Site, site_id)
        if site:
            site.county, site.program_type = county, "recuperative_care"
            site.notes = "POC placeholder county/program"
            session.add(site)
    session.commit()
    return len(DEMO)


SEED_POC_PATH = Path(__file__).with_name("seed_poc.json")


def seed_poc_beds(session: Session) -> int:
    """Load the committed POC placebo seed (facilities, beds, invented stays) into an empty DB. Returns beds created."""
    if session.exec(select(Bed)).first() is not None or not SEED_POC_PATH.exists():
        return 0
    data = json.loads(SEED_POC_PATH.read_text(encoding="utf-8"))
    today = datetime.now(timezone.utc).date()
    n = 0
    for f in data["facilities"]:
        session.add(Site(id=f["id"], name=f["name"], county=f["county"], program_type=f["program_type"],
                         active=f["active"], notes=f["notes"]))
        seen: set[str] = set()
        for b in f["beds"]:
            bed_id = slug(f"{f['id']}-{b['label']}")
            if bed_id in seen:
                continue
            seen.add(bed_id)
            session.add(Bed(id=bed_id, site_id=f["id"], room=b["label"], label=b["label"], status=b["status"],
                            notes=b.get("notes", ""), wheelchair_accessible=b["wheelchair"],
                            sex_designation=b["sex_designation"]))
            n += 1
            st = b.get("stay")
            if st:
                session.add(Assignment(
                    bed_id=bed_id, client_label=f"{st['first_name']} {st['last_name']}",
                    start_date=date.fromisoformat(st["intake_date"]) if st["intake_date"] else today,
                    expected_end_date=date.fromisoformat(st["expected_exit_date"]) if st["expected_exit_date"] else None,
                    source="seed", client_id=st["client_id"], first_name=st["first_name"], last_name=st["last_name"],
                    gender=st["gender"], intake_date=date.fromisoformat(st["intake_date"]) if st["intake_date"] else None,
                    payer=st["payer"], case_manager=st["case_manager"], stay_notes=st["notes"],
                    extra_json=json.dumps(st["extra"])))
    session.commit()
    return n
