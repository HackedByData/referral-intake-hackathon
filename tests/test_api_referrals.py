import pymupdf
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.db import make_engine, set_engine
from app.workflow.models import Referral
from app.workflow.pipeline import get_pipeline
from tests.factories import make_analysis, make_bed_option


class FakePipeline:
    def __init__(self, engine):
        self.engine = engine

    async def run(self, referral_id):
        with Session(self.engine) as s:
            from app.beds import service
            service.hold(s, "demo-site-a-102-a", referral_id, 4)
            ref = s.get(Referral, referral_id)
            ref.status = "ready_for_review"
            ref.packet_hash = "testhash"
            ref.result_json = make_analysis("approve", make_bed_option("demo-site-a-102-a")).model_dump_json()
            s.add(ref)
            s.commit()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("SEED_MODE", "demo")
    engine = make_engine("sqlite://")
    set_engine(engine)
    from app.main import app
    app.dependency_overrides[get_pipeline] = lambda: FakePipeline(engine)
    with TestClient(app) as c:  # runs lifespan: init_db + seed
        yield c
    app.dependency_overrides.clear()


def pdf_bytes() -> bytes:
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "invented test page")
    return doc.tobytes()


def upload(client):
    return client.post("/api/referrals", files={"file": ("packet.pdf", pdf_bytes(), "application/pdf")})


def test_health(client):
    assert client.get("/api/health").json()["api_key_configured"] is True


def test_upload_runs_pipeline_and_summary_view(client):
    r = upload(client)
    assert r.status_code == 202
    rid = r.json()["id"]
    view = client.get(f"/api/referrals/{rid}").json()
    assert view["status"] == "ready_for_review" and view["decision"] == "approve"
    assert view["recommendation"]["held_bed"]["bed_id"] == "demo-site-a-102-a"
    assert view["one_liner"].startswith("Adult man")
    assert [x["id"] for x in client.get("/api/referrals").json()] == [rid]


def test_details_join_rule_text(client):
    rid = upload(client).json()["id"]
    d = client.get(f"/api/referrals/{rid}/details").json()
    mob = next(r for r in d["rule_results"] if r["rule_id"] == "MOB-3")
    assert mob["kind"] == "must" and "100 ft" in mob["text"]
    assert d["pages"][0]["page"] == 1
    assert client.get(f"/api/referrals/{rid}/pages/2").json()["markdown"].startswith("PHYSICAL THERAPY")
    assert client.get(f"/api/referrals/{rid}/pages/99").status_code == 404


def test_page_image(client):
    rid = upload(client).json()["id"]
    r = client.get(f"/api/referrals/{rid}/pages/1/image")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"


def test_decision_flow_and_conflict(client):
    rid = upload(client).json()["id"]
    r = client.post(f"/api/referrals/{rid}/decision", json={"decision": "approve", "coordinator": "C"})
    assert r.status_code == 200 and r.json()["status"] == "approved"
    assert r.json()["coordinator_decision"]["agreed_with_ai"] is True
    again = client.post(f"/api/referrals/{rid}/decision", json={"decision": "approve", "coordinator": "C"})
    assert again.status_code == 409
    drafts = client.get(f"/api/referrals/{rid}/drafts").json()
    assert drafts["kipu_reference"] == f"MOCK-KIPU-{rid}"
    actions = [e["action"] for e in client.get("/api/audit", params={"referral_id": rid}).json()]
    assert actions[0] == "uploaded" and actions[-1] == "coordinator_decision"


def test_bed_matches(client):
    rid = upload(client).json()["id"]
    ids = [o["bed_id"] for o in client.get(f"/api/referrals/{rid}/bed-matches").json()]
    assert "demo-site-a-102-a" in ids  # own hold still counts as available


def test_non_pdf_upload_is_400_and_leaves_no_row(client):
    r = client.post("/api/referrals", files={"file": ("x.pdf", b"PK\x03\x04 docx", "application/pdf")})
    assert r.status_code == 400
    assert client.get("/api/referrals").json() == []
    r = client.post("/api/referrals", files={"file": ("empty.pdf", b"", "application/pdf")})
    assert r.status_code == 400


def test_upload_without_key_is_503(client, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    assert upload(client).status_code == 503


def test_retry_only_when_failed(client):
    rid = upload(client).json()["id"]
    assert client.post(f"/api/referrals/{rid}/retry").status_code == 409


def test_page_image_out_of_range_is_404(client):
    rid = upload(client).json()["id"]
    assert client.get(f"/api/referrals/{rid}/pages/0/image").status_code == 404
    assert client.get(f"/api/referrals/{rid}/pages/99/image").status_code == 404


def test_page_image_without_result_checks_pdf_page_count(client):
    from sqlmodel import Session as S
    from app.db import get_engine
    rid = upload(client).json()["id"]
    with S(get_engine()) as s:
        ref = s.get(Referral, rid)
        ref.result_json = None
        s.add(ref)
        s.commit()
    assert client.get(f"/api/referrals/{rid}/pages/99/image").status_code == 404
    assert client.get(f"/api/referrals/{rid}/pages/1/image").status_code == 200


class NoCountyPipeline:
    """Analysis finished with the county unknown: nothing is held."""
    def __init__(self, engine):
        self.engine = engine

    async def run(self, referral_id):
        from app.schemas import BedNeeds, CountySuggestion
        a = make_analysis("approve")
        a.summary.bed_needs = BedNeeds(sex="male", county=None)
        a.summary.suggested_county = CountySuggestion(basis="No health plan named in the packet.")
        with Session(self.engine) as s:
            ref = s.get(Referral, referral_id)
            ref.status = "ready_for_review"
            ref.result_json = a.model_dump_json()
            s.add(ref)
            s.commit()


@pytest.fixture
def nc_client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    engine = make_engine("sqlite://")
    set_engine(engine)
    from app.main import app
    app.dependency_overrides[get_pipeline] = lambda: NoCountyPipeline(engine)
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_confirm_county_holds_bed_and_audits(nc_client):
    rid = upload(nc_client).json()["id"]
    assert nc_client.get(f"/api/referrals/{rid}").json()["county"]["county"] is None
    r = nc_client.post(f"/api/referrals/{rid}/county", json={"county": "Orange", "coordinator": "Pat"})
    assert r.status_code == 200
    v = r.json()
    assert v["county"]["county"] == "Orange" and v["county"]["confirmed"] is True
    assert v["county"]["confirmed_by"] == "Pat" and v["county"]["basis"].startswith("Confirmed by Pat; AI suggestion: ")
    assert v["recommendation"]["held_bed"] is not None
    assert not any(x["kind"] == "county_needed" for x in v["recommendation"]["review_reasons"])
    actions = [e["action"] for e in nc_client.get("/api/audit", params={"referral_id": rid}).json()]
    assert "county_confirmed" in actions


def test_changing_county_releases_old_hold(nc_client):
    from app.beds import service
    from app.db import get_engine
    rid = upload(nc_client).json()["id"]
    first = nc_client.post(f"/api/referrals/{rid}/county", json={"county": "Orange", "coordinator": "Pat"}).json()
    old = first["recommendation"]["held_bed"]["bed_id"]
    second = nc_client.post(f"/api/referrals/{rid}/county", json={"county": "Los Angeles", "coordinator": "Pat"}).json()
    new = second["recommendation"]["held_bed"]["bed_id"]
    assert new != old and second["county"]["county"] == "Los Angeles"
    with Session(get_engine()) as s:
        assert service.held_bed_id(s, rid) == new


def test_county_with_no_facility_clears_hold_and_says_no_bed(nc_client):
    rid = upload(nc_client).json()["id"]
    nc_client.post(f"/api/referrals/{rid}/county", json={"county": "Orange", "coordinator": "Pat"})
    v = nc_client.post(f"/api/referrals/{rid}/county", json={"county": "Nowhere", "coordinator": "Pat"}).json()
    assert v["recommendation"]["held_bed"] is None
    assert any(x["kind"] == "no_bed" for x in v["recommendation"]["review_reasons"])


def test_county_409_after_approval_and_404(nc_client):
    rid = upload(nc_client).json()["id"]
    nc_client.post(f"/api/referrals/{rid}/county", json={"county": "Orange", "coordinator": "Pat"})
    assert nc_client.post(f"/api/referrals/{rid}/decision", json={"decision": "approve", "coordinator": "C"}).status_code == 200
    assert nc_client.post(f"/api/referrals/{rid}/county", json={"county": "Orange", "coordinator": "Pat"}).status_code == 409
    assert nc_client.post("/api/referrals/R-NOPE/county", json={"county": "Orange", "coordinator": "Pat"}).status_code == 404
