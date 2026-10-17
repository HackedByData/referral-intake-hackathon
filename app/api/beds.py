"""Bed management HTTP routes."""
from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlmodel import Session

from app.beds import census, service
from app.beds.importer import import_beds
from app.db import get_session
from app.schemas import (
    AssignmentView, BedCreate, BedDetail, BedImportReport, BedSummary, BedUpdate, BedView, CensusImportReport,
    FacilityUpdate, FacilityView,
)

MAX_CENSUS_BYTES = 20 * 1024 * 1024
router = APIRouter(prefix="/api", tags=["beds"])


@router.get("/beds", response_model=list[BedView])
def list_beds(site: str | None = None, status: str | None = None, session: Session = Depends(get_session)):
    return service.list_beds(session, site=site, status=status)


@router.get("/beds/summary", response_model=BedSummary)
def bed_summary(session: Session = Depends(get_session)):
    return service.summary(session)


@router.get("/beds/{bed_id}", response_model=BedDetail)
def bed_detail(bed_id: str, session: Session = Depends(get_session)):
    try:
        return service.bed_detail(session, bed_id)
    except service.BedNotFound:
        raise HTTPException(404, f"Bed {bed_id} not found")


@router.get("/facilities", response_model=list[FacilityView])
def list_facilities(session: Session = Depends(get_session)):
    return service.list_facilities(session)


@router.patch("/facilities/{facility_id}", response_model=FacilityView)
def update_facility(facility_id: str, data: FacilityUpdate, session: Session = Depends(get_session)):
    try:
        return service.update_facility(session, facility_id, data)
    except service.FacilityNotFound:
        raise HTTPException(404, f"Facility {facility_id} not found")


@router.post("/census/import", response_model=CensusImportReport)
def import_census_file(file: UploadFile, session: Session = Depends(get_session)):
    if not (file.filename or "").lower().endswith(".xlsx"):
        raise HTTPException(400, "Upload an .xlsx census workbook")
    data = file.file.read(MAX_CENSUS_BYTES + 1)
    if len(data) > MAX_CENSUS_BYTES:
        raise HTTPException(413, "Census workbook is larger than 20 MB")
    try:
        sheets, skipped = census.parse_workbook(data)
        report = census.import_census(session, sheets)  # single commit at the end
    except Exception as e:
        session.rollback()
        raise HTTPException(400, f"Census import failed: {e}")
    report.skipped_sheets = skipped
    return report


@router.post("/beds", response_model=BedView, status_code=201)
def create_bed(data: BedCreate, session: Session = Depends(get_session)):
    try:
        return service.to_view(session, service.create_bed(session, data))
    except service.BedExists:
        raise HTTPException(409, "A bed with this id already exists")


@router.patch("/beds/{bed_id}", response_model=BedView)
def update_bed(bed_id: str, data: BedUpdate, session: Session = Depends(get_session)):
    try:
        return service.to_view(session, service.update_bed(session, bed_id, data))
    except service.BedNotFound:
        raise HTTPException(404, f"Bed {bed_id} not found")
    except service.BedUnavailable as e:
        raise HTTPException(409, str(e))


@router.post("/beds/import", response_model=BedImportReport)
async def import_bed_file(file: UploadFile, session: Session = Depends(get_session)):
    return import_beds(session, file.filename or "", await file.read())


@router.get("/assignments", response_model=list[AssignmentView])
def list_assignments(active: bool = True, session: Session = Depends(get_session)):
    return service.list_assignments(session, active_only=active)


@router.post("/assignments/{assignment_id}/discharge", response_model=AssignmentView)
def discharge(assignment_id: int, session: Session = Depends(get_session)):
    try:
        return AssignmentView(**service.discharge(session, assignment_id).model_dump())
    except service.BedNotFound:
        raise HTTPException(404, f"Assignment {assignment_id} not found")
