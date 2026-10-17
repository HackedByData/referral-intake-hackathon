from fastapi.testclient import TestClient

from app.main import app


def test_criteria_lists_every_rule_with_precedence():
    body = TestClient(app).get("/api/criteria").json()
    ids = {r["id"] for r in body["rules"]}
    assert {"GEN-1", "MOB-3", "INT-E1", "INT-X1", "DOC-1", "FORM-M1"} <= ids
    superseded = {r["id"]: r["superseded_by"] for r in body["rules"] if r["superseded_by"]}
    assert superseded == {"FORM-M6": "MOB-3, MOB-4", "FORM-X3": "MOB-3, MOB-4", "FORM-X6": "OTH-2"}
    assert "outline wins" in body["precedence"]
    assert body["kinds"]["exclude"]
