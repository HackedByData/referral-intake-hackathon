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


def test_discharge_twice_does_not_free_reassigned_bed(session):
    a1 = service.assign(session, "M1", "R1", client_label="R1")
    service.discharge(session, a1.id)
    service.assign(session, "M1", "R2", client_label="R2")
    service.discharge(session, a1.id)
    assert session.get(Bed, "M1").status == "occupied"


def test_update_status_on_assigned_bed_refused(session):
    service.assign(session, "M1", "R1", client_label="R1")
    with pytest.raises(service.BedUnavailable):
        service.update_bed(session, "M1", BedUpdate(status="available"))
    service.update_bed(session, "M1", BedUpdate(notes="ok"))


def test_create_duplicate_bed_raises(session):
    with pytest.raises(service.BedExists):
        service.create_bed(session, BedCreate(site="Site A (demo)", room="101", label="A", id="M1"))
