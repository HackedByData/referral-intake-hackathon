"""Lesson persistence and the staff review workflow. Only `approved` lessons ever influence anything."""
import json
from datetime import datetime, timezone
from typing import Literal

from sqlmodel import Session, select

from app.knowledge.models import Lesson
from app.schemas import CaseProfile, LessonKind, LessonReview, LessonStatus, LessonView
from app.workflow import audit

Action = Literal["approve", "reject", "retire"]
TARGET: dict[str, LessonStatus] = {"approve": "approved", "reject": "rejected", "retire": "retired"}
ALLOWED_FROM = {"approve": "proposed", "reject": "proposed", "retire": "approved"}


class LessonError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def create(session: Session, kind: LessonKind, profile: CaseProfile, payload: dict, note: str, created_by: str,
           referral_id: str | None) -> Lesson:
    note = (note or "").strip()
    lesson = Lesson(kind=kind, status="proposed", created_at=_now(), created_by=created_by,
                    source_referral_id=referral_id, profile_json=profile.model_dump_json(),
                    payload_json=json.dumps(payload), note=note, needs_note=not note)
    session.add(lesson)
    session.commit()
    session.refresh(lesson)
    audit.record(session, "lesson_proposed", actor=created_by, referral_id=referral_id, lesson_id=lesson.id,
                 kind=kind)
    return lesson


def summary_text(kind: str, p: dict) -> str:
    if kind == "decision_precedent":
        return f"AI recommended {p.get('ai_decision', '?')}; coordinator decided {p.get('coordinator_decision', '?')}"
    if kind == "rule_interpretation":
        return (f"{p.get('rule_id', '?')}: AI said {p.get('ai_status', '?')}, "
                f"coordinator said {p.get('coordinator_status', '?')}")
    if kind == "bed_preference":
        return (f"Coordinator chose {p.get('chosen', {}).get('facility', '?')} over "
                f"{p.get('held', {}).get('facility', '?')}")
    if kind == "county_mapping":
        return (f"Plan '{p.get('health_plan', '?')}': AI said {p.get('ai_county') or 'no county'}, "
                f"coordinator said {p.get('coordinator_county', '?')}")
    return kind


def to_view(lesson: Lesson) -> LessonView:
    payload = json.loads(lesson.payload_json or "{}")
    return LessonView(id=lesson.id, kind=lesson.kind, status=lesson.status, summary=summary_text(lesson.kind, payload),
                      created_at=lesson.created_at, created_by=lesson.created_by,
                      source_referral_id=lesson.source_referral_id,
                      profile=CaseProfile.model_validate_json(lesson.profile_json), payload=payload,
                      note=lesson.note, needs_note=lesson.needs_note, reviewed_by=lesson.reviewed_by,
                      reviewed_at=lesson.reviewed_at, times_applied=lesson.times_applied)


def list_lessons(session: Session, status: str | None = None, kind: str | None = None) -> list[LessonView]:
    q = select(Lesson).order_by(Lesson.id)
    if status:
        q = q.where(Lesson.status == status)
    if kind:
        q = q.where(Lesson.kind == kind)
    return [to_view(l) for l in session.exec(q).all()]


def review(session: Session, lesson_id: int, action: Action, body: LessonReview) -> LessonView:
    lesson = session.get(Lesson, lesson_id)
    if lesson is None:
        raise LessonError(404, f"Lesson {lesson_id} not found")
    need = ALLOWED_FROM[action]
    if lesson.status != need:
        raise LessonError(409, f"Cannot {action} a lesson that is '{lesson.status}' (must be '{need}')")
    lesson.status, lesson.reviewed_by, lesson.reviewed_at = TARGET[action], body.reviewer, _now()
    if body.note is not None and body.note.strip():
        lesson.note, lesson.needs_note = body.note.strip(), False
    session.add(lesson)
    session.commit()
    session.refresh(lesson)
    audit.record(session, f"lesson_{TARGET[action]}", actor=body.reviewer, referral_id=lesson.source_referral_id,
                 lesson_id=lesson.id, kind=lesson.kind)
    return to_view(lesson)


def approved(session: Session, kind: str) -> list[Lesson]:
    return list(session.exec(select(Lesson).where(Lesson.status == "approved", Lesson.kind == kind)
                             .order_by(Lesson.id)).all())


def mark_applied(session: Session, ids: list[int]) -> None:
    now = _now()
    for lid in set(ids):
        lesson = session.get(Lesson, lid)
        if lesson is not None and lesson.status == "approved":
            lesson.times_applied += 1
            lesson.last_applied_at = now
            session.add(lesson)
    session.commit()
