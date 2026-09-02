import pytest

from qr_bench import config, db


@pytest.fixture
def test_db(tmp_path, monkeypatch):
    """Points qr_bench.db at a throwaway sqlite file for the duration of a test."""
    db_path = tmp_path / "test.db"
    monkeypatch.setattr(config, "DB_PATH", db_path)
    db.init_db()
    yield db_path
