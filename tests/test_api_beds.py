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


def test_conflicts_return_409(client):
    with Session(client.engine) as s:
        service.assign(s, "demo-site-a-102-a", "R1", client_label="R1")
    r = client.patch("/api/beds/demo-site-a-102-a", json={"status": "available"})
    assert r.status_code == 409
    r = client.post("/api/beds", json={"site": "Demo Site A", "room": "102", "label": "A"})
    assert r.status_code == 409


XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _workbook() -> bytes:
    from tests.test_census import build_workbook
    return build_workbook()


def _import(client):
    return client.post("/api/census/import", files={"file": ("census.xlsx", _workbook(), XLSX)})


def test_census_import_and_facilities(client):
    r = _import(client)
    assert r.status_code == 200
    rep = r.json()
    assert rep["facilities_created"] == 3 and any(s.startswith("Empty") for s in rep["skipped_sheets"])
    assert {s["sheet"] for s in rep["sheets"]} == {"RC Test", "Twin Test", "Junk Test"}
    facs = {f["id"]: f for f in client.get("/api/facilities").json()}
    assert facs["rc-test"]["total_beds"] == 6 and facs["rc-test"]["program_type"] == "recuperative_care"
    assert facs["rc-test"]["county"] is None
    assert "demo-site-a" in facs  # seeded demo sites are listed too


def test_census_import_rejects_non_xlsx(client):
    r = client.post("/api/census/import", files={"file": ("census.csv", b"a,b", "text/csv")})
    assert r.status_code == 400
    r = client.post("/api/census/import", files={"file": ("census.xlsx", b"not a zip", XLSX)})
    assert r.status_code == 400


def test_patch_facility_county_enables_matching(client):
    from app.schemas import BedNeeds
    _import(client)
    needs = BedNeeds(sex="female", county="Orange")
    with Session(client.engine) as s:
        assert not [o for o in service.find_matches(s, needs, limit=50) if o.site == "RC Test"]
    r = client.patch("/api/facilities/rc-test", json={"county": "Orange"})
    assert r.status_code == 200 and r.json()["county"] == "Orange"
    with Session(client.engine) as s:
        assert [o for o in service.find_matches(s, needs, limit=50) if o.site == "RC Test"]
    assert client.patch("/api/facilities/nope", json={"county": "x"}).status_code == 404


def test_bed_detail_has_stay_extra(client):
    _import(client)
    d = client.get("/api/beds/rc-test-101-a").json()
    assert d["bed"]["stay"]["first_name"] == "Alpha" and d["bed"]["status"] == "occupied"
    assert d["current_stay"]["extra"]["PETS"] == "No" and d["current_stay"]["status"] == "active"
    assert client.get("/api/beds/rc-test-101-b").json()["current_stay"] is None
    assert client.get("/api/beds/nope").status_code == 404



def test_census_import_oversize_413(client):
    big = b"0" * (20 * 1024 * 1024 + 1)
    r = client.post("/api/census/import", files={"file": ("census.xlsx", big, XLSX)})
    assert r.status_code == 413


def test_census_import_failure_rolls_back_400(client, monkeypatch):
    from app.beds import census as census_mod

    def boom(session, sheets, program_hint=True):
        raise RuntimeError("kaboom")
    monkeypatch.setattr(census_mod, "import_census", boom)
    r = client.post("/api/census/import", files={"file": ("census.xlsx", _workbook(), XLSX)})
    assert r.status_code == 400 and "Census import failed" in r.json()["detail"]

