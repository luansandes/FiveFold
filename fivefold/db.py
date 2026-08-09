from __future__ import annotations

from collections.abc import Generator
from functools import lru_cache

from sqlalchemy import create_engine, inspect, text
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
    from fivefold.operational import current_operational_setting
    from fivefold.pricing import DEFAULT_PRICING

    Base.metadata.create_all(get_engine())
    _apply_compatibility_migrations()
    session = get_session_factory()()
    try:
        if not session.scalar(select(models.PricingSetting).limit(1)):
            session.add(models.PricingSetting(name="default", values=DEFAULT_PRICING))
        current_operational_setting(session)
        session.commit()
    finally:
        session.close()


def _apply_compatibility_migrations() -> None:
    """Apply the small additive schema changes required by pre-Alembic deployments."""
    engine = get_engine()
    columns = {column["name"] for column in inspect(engine).get_columns("research_runs")}
    statements: list[str] = []
    if "operational_setting_id" not in columns:
        statements.append("ALTER TABLE research_runs ADD COLUMN operational_setting_id VARCHAR(36)")
    if "opportunity_threshold" not in columns:
        statements.append(
            "ALTER TABLE research_runs ADD COLUMN opportunity_threshold INTEGER NOT NULL DEFAULT 90"
        )
    if "created_count" not in columns:
        statements.append(
            "ALTER TABLE research_runs ADD COLUMN created_count INTEGER NOT NULL DEFAULT 0"
        )
    if "duplicates_skipped" not in columns:
        statements.append(
            "ALTER TABLE research_runs ADD COLUMN duplicates_skipped INTEGER NOT NULL DEFAULT 0"
        )
    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))
        duplicate_groups = connection.execute(
            text(
                "SELECT COUNT(*) FROM (SELECT place_id FROM prospects "
                "WHERE place_id IS NOT NULL GROUP BY place_id HAVING COUNT(*) > 1) duplicates"
            )
        ).scalar_one()
        if not duplicate_groups:
            connection.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS ux_prospects_place_id "
                    "ON prospects (place_id) WHERE place_id IS NOT NULL"
                )
            )


def reset_db_caches() -> None:
    get_session_factory.cache_clear()
    engine = get_engine()
    engine.dispose()
    get_engine.cache_clear()
