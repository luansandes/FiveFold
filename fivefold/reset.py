from __future__ import annotations

from typing import Any

from sqlalchemy import Engine, inspect, text

GENERATED_TABLES = (
    "audit_events",
    "preview_tokens",
    "stage_runs",
    "handoffs",
    "jobs",
    "artifacts",
    "pipeline_runs",
    "prospects",
    "research_runs",
    "prompt_versions",
    "operational_settings",
)


def reset_generated_data(engine: Engine) -> dict[str, Any]:
    existing = set(inspect(engine).get_table_names())
    counts: dict[str, int] = {}
    with engine.begin() as connection:
        for table in GENERATED_TABLES:
            if table not in existing:
                continue
            counts[table] = int(
                connection.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
            )
            connection.execute(text(f"DELETE FROM {table}"))
    return {"deleted": counts, "preserved": ["pricing_settings"]}
