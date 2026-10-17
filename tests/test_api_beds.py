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
