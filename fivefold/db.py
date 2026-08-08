from __future__ import annotations

from collections.abc import Generator
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from fivefold.config import get_settings


class Base(DeclarativeBase):
    pass


@lru_cache
def get_engine():  # type: ignore[no-untyped-def]
    settings = get_settings()
    connect_args = (
        {"check_same_thread": False}
        if settings.effective_database_url.startswith("sqlite")
        else {"connect_timeout": 10}
    )
    return create_engine(
        settings.effective_database_url,
        pool_pre_ping=True,
        connect_args=connect_args,
    )


@lru_cache
def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


def init_db() -> None:
    from sqlalchemy import select

    from fivefold import models
    from fivefold.pricing import DEFAULT_PRICING

    Base.metadata.create_all(get_engine())
    session = get_session_factory()()
    try:
        if not session.scalar(select(models.PricingSetting).limit(1)):
            session.add(models.PricingSetting(name="default", values=DEFAULT_PRICING))
            session.commit()
    finally:
        session.close()


def reset_db_caches() -> None:
    get_session_factory.cache_clear()
    engine = get_engine()
    engine.dispose()
    get_engine.cache_clear()
