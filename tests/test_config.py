from pathlib import Path

from app.config import ROOT, get_settings


def test_data_dir_defaults_to_repo_data_locally(monkeypatch):
    monkeypatch.delenv("DATA_DIR", raising=False)
    monkeypatch.delenv("VERCEL", raising=False)
    assert get_settings().data_dir == ROOT / "data"


def test_data_dir_defaults_to_tmp_on_vercel(monkeypatch):
    # Vercel Functions have a read-only filesystem except /tmp.
    monkeypatch.delenv("DATA_DIR", raising=False)
    monkeypatch.setenv("VERCEL", "1")
    assert get_settings().data_dir == Path("/tmp/raft-data")


def test_explicit_data_dir_wins(monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setenv("DATA_DIR", "/somewhere")
    assert get_settings().data_dir == Path("/somewhere")
