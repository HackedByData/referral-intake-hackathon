"""Staff review of lessons learned from coordinator overrides."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from app.db import get_session
from app.knowledge import store
from app.schemas import LessonKind, LessonReview, LessonStatus, LessonView

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


@router.get("", response_model=list[LessonView])
def list_lessons(status: LessonStatus | None = None, kind: LessonKind | None = None,
                 session: Session = Depends(get_session)):
    return store.list_lessons(session, status=status, kind=kind)


@router.get("/export")
def export(session: Session = Depends(get_session)):
    lessons = store.list_lessons(session)
    return {"exported_at": datetime.now(timezone.utc).isoformat(), "count": len(lessons),
            "lessons": [l.model_dump(mode="json") for l in lessons]}


def _act(action: str, lesson_id: int, body: LessonReview, session: Session) -> LessonView:
    try:
        return store.review(session, lesson_id, action, body)
    except store.LessonError as e:
        raise HTTPException(e.status_code, str(e))


@router.post("/{lesson_id}/approve", response_model=LessonView)
def approve(lesson_id: int, body: LessonReview, session: Session = Depends(get_session)):
    return _act("approve", lesson_id, body, session)


@router.post("/{lesson_id}/reject", response_model=LessonView)
def reject(lesson_id: int, body: LessonReview, session: Session = Depends(get_session)):
    return _act("reject", lesson_id, body, session)


@router.post("/{lesson_id}/retire", response_model=LessonView)
def retire(lesson_id: int, body: LessonReview, session: Session = Depends(get_session)):
    return _act("retire", lesson_id, body, session)
