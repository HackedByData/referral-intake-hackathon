from sqlalchemy import inspect, text

from app import db
from app.db import init_db, make_engine, set_engine


def test_init_db_backs_up_incompatible_file_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    old = make_engine()  # file DB at tmp_path/app.db
    with old.begin() as conn:
        conn.execute(text("CREATE TABLE site (id VARCHAR PRIMARY KEY, name VARCHAR)"))
    set_engine(old)
    try:
        new = init_db()
        backups = list(tmp_path.glob("app.db.bak-*"))
        assert len(backups) == 1
        cols = {c["name"] for c in inspect(new).get_columns("site")}
        assert "county" in cols
        assert db.get_engine() is new
    finally:
        db.get_engine().dispose()
        set_engine(make_engine("sqlite://"))


def test_init_db_in_memory_just_creates():
    engine = make_engine("sqlite://")
    set_engine(engine)
    assert init_db() is engine
    assert "county" in {c["name"] for c in inspect(engine).get_columns("site")}
