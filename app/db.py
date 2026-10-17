"""SQLite engine and session helpers. Table models live in app/beds/models.py and app/workflow/models.py."""
from collections.abc import Iterator

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.config import get_settings

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


def init_db(engine=None) -> None:
    import app.beds.models  # noqa: F401  (registers tables)
    import app.workflow.models  # noqa: F401

    SQLModel.metadata.create_all(engine or get_engine())


def get_session() -> Iterator[Session]:
    with Session(get_engine()) as session:
        yield session
