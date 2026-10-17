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
