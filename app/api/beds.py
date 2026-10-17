"""Bed management HTTP routes."""
from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlmodel import Session

from app.beds import service
from app.beds.importer import import_beds
from app.db import get_session
from app.schemas import AssignmentView, BedCreate, BedImportReport, BedSummary, BedUpdate, BedView

router = APIRouter(prefix="/api", tags=["beds"])


@router.get("/beds", response_model=list[BedView])
def list_beds(site: str | None = None, status: str | None = None, session: Session = Depends(get_session)):
    return service.list_beds(session, site=site, status=status)


@router.get("/beds/summary", response_model=BedSummary)
def bed_summary(session: Session = Depends(get_session)):
    return service.summary(session)


@router.post("/beds", response_model=BedView, status_code=201)
def create_bed(data: BedCreate, session: Session = Depends(get_session)):
    return service.to_view(session, service.create_bed(session, data))


@router.patch("/beds/{bed_id}", response_model=BedView)
def update_bed(bed_id: str, data: BedUpdate, session: Session = Depends(get_session)):
    try:
        return service.to_view(session, service.update_bed(session, bed_id, data))
    except service.BedNotFound:
        raise HTTPException(404, f"Bed {bed_id} not found")


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
