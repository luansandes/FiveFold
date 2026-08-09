from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from fivefold.config import get_settings
from fivefold.db import get_session_factory, init_db, reset_db_caches


@pytest.fixture
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[Session, None, None]:
    database_path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path.as_posix()}")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("BASE_URL", "http://testserver")
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setenv("GOOGLE_MAPS_API_KEY", "test-google-key")
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", "test-password-hash")
    get_settings.cache_clear()
    reset_db_caches()
    init_db()
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()
        reset_db_caches()
        get_settings.cache_clear()
