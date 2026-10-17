import io

import openpyxl
import pytest
from sqlmodel import Session, SQLModel, select

import app.beds.models  # noqa: F401
from app.beds.importer import import_beds, read_rows
from app.beds.models import Bed
from app.beds.seed import seed_demo_beds
from app.db import make_engine

CSV = b"""Site,Room,Bed,Gender,Wheelchair,O2,Status,Notes
Demo North,101,A,M,yes,,Available,
Demo North,101,B,M,no,yes,Occupied,
Demo North,,C,F,,,Available,missing room
"""


@pytest.fixture
def session():
    engine = make_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def test_csv_import_maps_columns_and_reports_errors(session):
    report = import_beds(session, "beds.csv", CSV)
    assert report.created == 2 and report.updated == 0
    assert len(report.errors) == 1 and "row 4" in report.errors[0]
    a = session.get(Bed, "demo-north-101-a")
    assert a.sex_designation == "male" and a.wheelchair_accessible and not a.oxygen_ok
    b = session.get(Bed, "demo-north-101-b")
    assert b.status == "occupied" and b.oxygen_ok


def test_reimport_updates(session):
    import_beds(session, "beds.csv", CSV)
    report = import_beds(session, "beds.csv", CSV.replace(b"Occupied", b"Available"))
    assert report.updated == 2 and report.created == 0
    assert session.get(Bed, "demo-north-101-b").status == "available"


def test_xlsx_import(session):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Site", "Room", "Bed", "Gender"])
    ws.append(["Demo South", 5, "A", "Female"])
    buf = io.BytesIO()
    wb.save(buf)
    assert read_rows("beds.xlsx", buf.getvalue())[0]["room"] == "5"
    report = import_beds(session, "beds.xlsx", buf.getvalue())
    assert report.created == 1 and session.get(Bed, "demo-south-5-a").sex_designation == "female"


def test_unsupported_file_type(session):
    report = import_beds(session, "beds.txt", b"x")
    assert report.created == 0 and "Unsupported" in report.errors[0]


def test_seed_is_idempotent(session):
    n = seed_demo_beds(session)
    assert n >= 16
    assert seed_demo_beds(session) == 0
    beds = session.exec(select(Bed)).all()
    assert any(b.wheelchair_accessible for b in beds) and any(b.status == "occupied" for b in beds)


def test_ragged_csv_row_does_not_crash(session):
    report = import_beds(session, "beds.csv", b"Site,Room,Bed\nX,1,A,extra\n")
    assert report.created == 1 and report.errors == []


def test_corrupt_xlsx_returns_error(session):
    report = import_beds(session, "beds.xlsx", b"not a zip")
    assert report.created == 0 and len(report.errors) == 1


def test_reimport_without_status_column_keeps_status(session):
    import_beds(session, "beds.csv", b"Site,Room,Bed,Status\nX,1,A,Out of service\n")
    import_beds(session, "beds.csv", b"Site,Room,Bed\nX,1,A\n")
    assert session.get(Bed, "x-1-a").status == "out_of_service"


def test_reimport_never_frees_assigned_bed(session):
    from app.beds import service
    import_beds(session, "beds.csv", b"Site,Room,Bed,Status\nX,1,A,Available\n")
    service.assign(session, "x-1-a", "R1", client_label="R1")
    report = import_beds(session, "beds.csv", b"Site,Room,Bed,Status\nX,1,A,Available\n")
    assert session.get(Bed, "x-1-a").status == "occupied"
    assert any("active assignment" in e for e in report.errors)
