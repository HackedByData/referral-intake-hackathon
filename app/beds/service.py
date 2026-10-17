"""Bed management service. The ONLY interface other modules use for beds."""
import re
from datetime import date, datetime, timedelta, timezone

from sqlmodel import Session, select

from app.beds.models import Assignment, Bed, Hold, Site
from app.schemas import (
    AssignmentView, BedCreate, BedNeeds, BedOption, BedSummary, BedUpdate, BedView, SiteCount,
)


class BedUnavailable(Exception):
    pass


class BedNotFound(Exception):
    pass


class BedExists(Exception):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def _active_hold(session: Session, bed_id: str) -> Hold | None:
    now = utcnow()
    return session.exec(select(Hold).where(Hold.bed_id == bed_id, Hold.active == True,  # noqa: E712
                                           Hold.expires_at > now)).first()


def _active_assignment(session: Session, bed_id: str) -> Assignment | None:
    return session.exec(select(Assignment).where(Assignment.bed_id == bed_id, Assignment.status == "active")).first()


def effective_status(session: Session, bed: Bed) -> str:
    if bed.status != "available":
        return bed.status
    return "held" if _active_hold(session, bed.id) else "available"


def _is_available(session: Session, bed: Bed, for_referral: str | None) -> bool:
    if bed.status != "available":
        return False
    h = _active_hold(session, bed.id)
    return h is None or (for_referral is not None and h.referral_id == for_referral)


def _site_name(session: Session, site_id: str) -> str:
    site = session.get(Site, site_id)
    return site.name if site else site_id


def bed_option(session: Session, bed: Bed, notes: list[str] | None = None) -> BedOption:
    return BedOption(bed_id=bed.id, site=_site_name(session, bed.site_id), room=bed.room, label=bed.label,
                     match_notes=notes or [])


def _compatible(bed: Bed, needs: BedNeeds) -> list[str] | None:
    notes: list[str] = []
    if needs.sex == "unknown":
        if bed.sex_designation != "any":
            return None
        notes.append("any-gender room (patient sex not stated in packet)")
    elif bed.sex_designation not in ("any", needs.sex):
        return None
    else:
        notes.append(f"{bed.sex_designation} room" if bed.sex_designation != "any" else "any-gender room")
    if needs.wheelchair:
        if not bed.wheelchair_accessible:
            return None
        notes.append("wheelchair accessible")
    if needs.oxygen:
        if not bed.oxygen_ok:
            return None
        notes.append("oxygen OK")
    if needs.isolation:
        if not bed.isolation_ok:
            return None
        notes.append("isolation capable")
    if needs.mobility_aid and bed.ground_floor:
        notes.append("ground floor")
    return notes


MATCHABLE_PROGRAMS = ("recuperative_care",)


def _norm_county(c: str | None) -> str:
    return (c or "").strip().lower().removesuffix(" county")


def find_matches(session: Session, needs: BedNeeds, limit: int = 3, for_referral: str | None = None) -> list[BedOption]:
    """Hard filters: active recuperative-care facility in the patient's county. Unknown county -> no matches."""
    county = _norm_county(needs.county)
    if not county:
        return []
    sites = {s.id: s for s in session.exec(select(Site)).all()
             if s.active and s.program_type in MATCHABLE_PROGRAMS and _norm_county(s.county) == county}
    candidates = []
    for bed in session.exec(select(Bed)).all():
        if bed.site_id not in sites:
            continue
        if not _is_available(session, bed, for_referral):
            continue
        notes = _compatible(bed, needs)
        if notes is None:
            continue
        specialization = sum([bed.wheelchair_accessible, bed.oxygen_ok, bed.isolation_ok])
        wants_ground = bool(needs.mobility_aid or needs.wheelchair)
        candidates.append(((specialization, 0 if (wants_ground and bed.ground_floor) else 1,
                            bed.site_id, bed.room, bed.label), bed, notes))
    candidates.sort(key=lambda c: c[0])
    return [bed_option(session, bed, notes) for _, bed, notes in candidates[:limit]]


def release_hold(session: Session, referral_id: str) -> None:
    for h in session.exec(select(Hold).where(Hold.referral_id == referral_id, Hold.active == True)).all():  # noqa: E712
        h.active = False
        session.add(h)
    session.commit()


def held_bed_id(session: Session, referral_id: str) -> str | None:
    h = session.exec(select(Hold).where(Hold.referral_id == referral_id, Hold.active == True,  # noqa: E712
                                        Hold.expires_at > utcnow())).first()
    return h.bed_id if h else None


def hold(session: Session, bed_id: str, referral_id: str, ttl_hours: float) -> Hold:
    bed = session.get(Bed, bed_id)
    if bed is None:
        raise BedNotFound(bed_id)
    if not _is_available(session, bed, referral_id):
        raise BedUnavailable(f"Bed {bed_id} is not available")
    release_hold(session, referral_id)
    now = utcnow()
    h = Hold(bed_id=bed_id, referral_id=referral_id, created_at=now, expires_at=now + timedelta(hours=ttl_hours))
    session.add(h)
    session.commit()
    session.refresh(h)
    return h


def assign(session: Session, bed_id: str, referral_id: str, client_label: str,
           expected_end_date: date | None = None) -> Assignment:
    bed = session.get(Bed, bed_id)
    if bed is None:
        raise BedNotFound(bed_id)
    if not _is_available(session, bed, referral_id) or _active_assignment(session, bed_id):
        raise BedUnavailable(f"Bed {bed_id} is no longer available")
    for h in session.exec(select(Hold).where(Hold.referral_id == referral_id, Hold.active == True)).all():  # noqa: E712
        h.active = False
        session.add(h)
    bed.status = "occupied"
    session.add(bed)
    a = Assignment(bed_id=bed_id, referral_id=referral_id, client_label=client_label,
                   start_date=utcnow().date(), expected_end_date=expected_end_date)
    session.add(a)
    session.commit()
    session.refresh(a)
    return a


def discharge(session: Session, assignment_id: int) -> Assignment:
    a = session.get(Assignment, assignment_id)
    if a is None:
        raise BedNotFound(f"assignment {assignment_id}")
    if a.status == "released":
        return a
    a.status, a.released_at = "released", utcnow()
    session.add(a)
    session.flush()
    bed = session.get(Bed, a.bed_id)
    if bed is not None and bed.status == "occupied" and _active_assignment(session, bed.id) is None:
        bed.status = "available"
        session.add(bed)
    session.commit()
    session.refresh(a)
    return a


def _ensure_site(session: Session, name: str) -> Site:
    site_id = slug(name)
    site = session.get(Site, site_id)
    if site is None:
        site = Site(id=site_id, name=name)
        session.add(site)
        session.commit()
    return site


def create_bed(session: Session, data: BedCreate) -> Bed:
    bed_id = data.id or slug(f"{data.site}-{data.room}-{data.label}")
    if session.get(Bed, bed_id) is not None:
        raise BedExists(bed_id)
    site = _ensure_site(session, data.site)
    bed = Bed(id=bed_id, site_id=site.id, room=data.room, label=data.label,
              **data.model_dump(exclude={"id", "site", "room", "label"}))
    session.add(bed)
    session.commit()
    session.refresh(bed)
    return bed


def update_bed(session: Session, bed_id: str, data: BedUpdate) -> Bed:
    bed = session.get(Bed, bed_id)
    if bed is None:
        raise BedNotFound(bed_id)
    changes = data.model_dump(exclude_none=True)
    if "status" in changes and changes["status"] != bed.status and _active_assignment(session, bed_id):
        raise BedUnavailable(f"Bed {bed_id} has an active assignment")
    for k, v in changes.items():
        setattr(bed, k, v)
    session.add(bed)
    session.commit()
    session.refresh(bed)
    return bed


def to_view(session: Session, bed: Bed) -> BedView:
    h = _active_hold(session, bed.id)
    a = _active_assignment(session, bed.id)
    site = session.get(Site, bed.site_id)
    return BedView(id=bed.id, site=site.name if site else bed.site_id, site_id=bed.site_id,
                   county=site.county if site else None,
                   program_type=site.program_type if site else "unknown", room=bed.room, label=bed.label,
                   sex_designation=bed.sex_designation, wheelchair_accessible=bed.wheelchair_accessible,
                   ground_floor=bed.ground_floor, oxygen_ok=bed.oxygen_ok, isolation_ok=bed.isolation_ok,
                   status=effective_status(session, bed), notes=bed.notes,
                   referral_id=(a.referral_id if a else (h.referral_id if h else None)),
                   assignment_id=a.id if a else None, hold_expires_at=h.expires_at if h else None)


def list_beds(session: Session, site: str | None = None, status: str | None = None) -> list[BedView]:
    beds = session.exec(select(Bed).order_by(Bed.site_id, Bed.room, Bed.label)).all()
    views = [to_view(session, b) for b in beds]
    if site:
        views = [v for v in views if v.site == site or slug(v.site) == slug(site)]
    if status:
        views = [v for v in views if v.status == status]
    return views


def summary(session: Session) -> BedSummary:
    views = list_beds(session)
    count = lambda st: sum(1 for v in views if v.status == st)  # noqa: E731
    sites: dict[str, SiteCount] = {}
    for v in views:
        sc = sites.setdefault(v.site, SiteCount(site=v.site, total=0, available=0))
        sc.total += 1
        sc.available += v.status == "available"
    return BedSummary(total=len(views), available=count("available"), held=count("held"),
                      occupied=count("occupied"), out_of_service=count("out_of_service"), by_site=list(sites.values()),
                      needs_cleaning=count("needs_cleaning"))


def list_assignments(session: Session, active_only: bool = True) -> list[AssignmentView]:
    q = select(Assignment)
    if active_only:
        q = q.where(Assignment.status == "active")
    return [AssignmentView(**a.model_dump()) for a in session.exec(q).all()]
