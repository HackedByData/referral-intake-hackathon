"""SQLite engine and session helpers. Table models live in app/beds/models.py and app/workflow/models.py."""
import logging
import os
from collections.abc import Iterator
from datetime import datetime

from sqlalchemy import inspect
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.config import get_settings

log = logging.getLogger(__name__)
_engine = None


def make_engine(url: str | None = None):
    if url is None:
        settings = get_settings()
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        url = f"sqlite:///{settings.data_dir / 'app.db'}"
    kwargs: dict = {"connect_args": {"check_same_thread": False}}
    if url == "sqlite://":
        kwargs["poolclass"] = StaticPool  # one shared in-memory DB for tests
    return create_engine(url, **kwargs)


def get_engine():
    global _engine
    if _engine is None:
        _engine = make_engine()
    return _engine


def set_engine(engine) -> None:
    global _engine
    _engine = engine


def _schema_is_stale(engine) -> bool:
    insp = inspect(engine)
    existing = set(insp.get_table_names())
    for name, table in SQLModel.metadata.tables.items():
        if name in existing:
            have = {c["name"] for c in insp.get_columns(name)}
            if any(c.name not in have for c in table.columns):
                return True
    return False


def init_db(engine=None):
    """Create tables. A file DB with an older incompatible schema is backed up and recreated. Returns the engine."""
    import app.beds.models  # noqa: F401  (registers tables)
    import app.workflow.models  # noqa: F401

    explicit = engine is not None
    engine = engine or get_engine()
    path = engine.url.database
    url = engine.url.render_as_string(hide_password=False)
    if path and path != ":memory:" and _schema_is_stale(engine):
        engine.dispose()
        backup = f"{path}.bak-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        os.replace(path, backup)
        log.warning("Existing database schema is incompatible; backed up %s to %s and creating a fresh one", path, backup)
        engine = make_engine(url) if explicit else make_engine()
        set_engine(engine)
    SQLModel.metadata.create_all(engine)
    return engine


def get_session() -> Iterator[Session]:
    with Session(get_engine()) as session:
        yield session
