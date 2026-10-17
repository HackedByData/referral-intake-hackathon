"""Shared helpers for the knowledge-core tests. Invented data only."""
import pytest
from sqlmodel import Session, SQLModel

import app.beds.models  # noqa: F401
import app.knowledge.models  # noqa: F401
import app.workflow.models  # noqa: F401
from app.db import make_engine
from app.knowledge import store
from app.schemas import CaseProfile

PROFILE = CaseProfile(county="Orange", payer="CalOptima", sex="male", wheelchair=False, oxygen=False,
                      isolation=False, mobility_aid="walker", systems=["Integumentary"], flagged_rule_ids=["MOB-3"])


def bed_payload(held_fac="sa", chosen_fac="sb"):
    def side(fid):
        return {"facility_id": fid, "facility": f"Facility {fid}", "county": "Orange", "sex_designation": "male",
                "wheelchair_accessible": False, "oxygen_ok": False, "isolation_ok": False, "ground_floor": True}
    return {"held": side(held_fac), "chosen": side(chosen_fac)}


def approved(session, kind, payload, profile=PROFILE, note="because"):
    lesson = store.create(session, kind, profile, payload, note, "coord", "R1")
    store.review(session, lesson.id, "approve", __import__("app.schemas", fromlist=["x"]).LessonReview(reviewer="staff"))
    return lesson


@pytest.fixture
def session():
    eng = make_engine("sqlite://")
    SQLModel.metadata.create_all(eng)
    with Session(eng) as s:
        yield s
