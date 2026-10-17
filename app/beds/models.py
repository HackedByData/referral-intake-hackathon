"""Bed management tables. Stored bed.status is available|occupied|out_of_service; 'held' is derived from Hold."""
from datetime import date

from pydantic import NaiveDatetime
from sqlmodel import Field, SQLModel


class Site(SQLModel, table=True):
    id: str = Field(primary_key=True)
    name: str


class Bed(SQLModel, table=True):
    id: str = Field(primary_key=True)
    site_id: str = Field(foreign_key="site.id", index=True)
    room: str
    label: str
    sex_designation: str = "any"
    wheelchair_accessible: bool = False
    ground_floor: bool = False
    oxygen_ok: bool = False
    isolation_ok: bool = False
    status: str = "available"
    notes: str = ""


class Hold(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    bed_id: str = Field(foreign_key="bed.id", index=True)
    referral_id: str = Field(index=True)
    created_at: NaiveDatetime
    expires_at: NaiveDatetime
    active: bool = True


class Assignment(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    bed_id: str = Field(foreign_key="bed.id", index=True)
    referral_id: str = Field(index=True)
    client_label: str
    start_date: date
    expected_end_date: date | None = None
    released_at: NaiveDatetime | None = None
    status: str = "active"
