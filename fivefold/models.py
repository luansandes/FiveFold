from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from fivefold.db import Base


def new_id() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(UTC)


class ResearchRun(Base):
    __tablename__ = "research_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    location: Mapped[str] = mapped_column(String(200))
    categories: Mapped[list[str]] = mapped_column(JSON)
    max_businesses: Mapped[int] = mapped_column(Integer)
    operational_setting_id: Mapped[str | None] = mapped_column(
        ForeignKey("operational_settings.id"), nullable=True, index=True
    )
    opportunity_threshold: Mapped[int] = mapped_column(Integer, default=90)
    created_count: Mapped[int] = mapped_column(Integer, default=0)
    duplicates_skipped: Mapped[int] = mapped_column(Integer, default=0)
    provider: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class Prospect(Base):
    __tablename__ = "prospects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    research_run_id: Mapped[str] = mapped_column(ForeignKey("research_runs.id"), index=True)
    business_name: Mapped[str] = mapped_column(String(240))
    category: Mapped[str] = mapped_column(String(120))
    location: Mapped[str] = mapped_column(String(200), default="Dublin, Ireland")
    place_id: Mapped[str | None] = mapped_column(
        String(250), nullable=True, unique=True, index=True
    )
    website_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    footprint: Mapped[str] = mapped_column(String(30))
    qualification_reason: Mapped[str] = mapped_column(Text)
    current_stage: Mapped[str] = mapped_column(String(30), default="researcher", index=True)
    status: Mapped[str] = mapped_column(String(40), default="queued", index=True)
    priority: Mapped[str] = mapped_column(String(20), default="unscored")
    human_status: Mapped[str] = mapped_column(String(30), default="unverified")
    human_note: Mapped[str] = mapped_column(Text, default="")
    preview_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    revision_count: Mapped[int] = mapped_column(Integer, default=0)
    redacted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    artifacts: Mapped[list[Artifact]] = relationship(back_populates="prospect")
    stage_runs: Mapped[list[StageRun]] = relationship(back_populates="prospect")


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    prospect_id: Mapped[str] = mapped_column(ForeignKey("prospects.id"), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(40), default="queued")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(80), nullable=True)


class StageRun(Base):
    __tablename__ = "stage_runs"
    __table_args__ = (Index("ix_stage_run_prospect_stage", "prospect_id", "stage"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    prospect_id: Mapped[str] = mapped_column(ForeignKey("prospects.id"), index=True)
    stage: Mapped[str] = mapped_column(String(30), index=True)
    status: Mapped[str] = mapped_column(String(30), default="running")
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    prompt_version: Mapped[str] = mapped_column(String(64))
    input_artifact_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    output_artifact_id: Mapped[str | None] = mapped_column(
        ForeignKey("artifacts.id", use_alter=True), nullable=True
    )
    usage: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    prospect: Mapped[Prospect] = relationship(back_populates="stage_runs")


class Artifact(Base):
    __tablename__ = "artifacts"
    __table_args__ = (
        Index("ix_artifact_prospect_stage_version", "prospect_id", "stage", "version", unique=True),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    prospect_id: Mapped[str] = mapped_column(ForeignKey("prospects.id"), index=True)
    stage: Mapped[str] = mapped_column(String(30), index=True)
    version: Mapped[int] = mapped_column(Integer)
    stale: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    prospect: Mapped[Prospect] = relationship(back_populates="artifacts")


class Handoff(Base):
    __tablename__ = "handoffs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    prospect_id: Mapped[str] = mapped_column(ForeignKey("prospects.id"), index=True)
    from_stage: Mapped[str] = mapped_column(String(30))
    to_stage: Mapped[str | None] = mapped_column(String(30), nullable=True)
    decision: Mapped[str] = mapped_column(String(40))
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_job_claim", "status", "available_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    prospect_id: Mapped[str] = mapped_column(ForeignKey("prospects.id"), index=True)
    stage: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    idempotency_key: Mapped[str] = mapped_column(String(160), unique=True)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PreviewToken(Base):
    __tablename__ = "preview_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    prospect_id: Mapped[str] = mapped_column(ForeignKey("prospects.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    artifact_id: Mapped[str] = mapped_column(ForeignKey("artifacts.id"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PromptVersion(Base):
    __tablename__ = "prompt_versions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    stage: Mapped[str] = mapped_column(String(30), index=True)
    version_hash: Mapped[str] = mapped_column(String(64), unique=True)
    prompt: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PricingSetting(Base):
    __tablename__ = "pricing_settings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(80), unique=True, default="default")
    values: Mapped[dict[str, Any]] = mapped_column(JSON)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class OperationalSetting(Base):
    __tablename__ = "operational_settings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    max_prospects_per_run: Mapped[int] = mapped_column(Integer, default=1)
    opportunity_score_threshold: Mapped[int] = mapped_column(Integer, default=90)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SystemMarker(Base):
    __tablename__ = "system_markers"

    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_prospect_created", "prospect_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    prospect_id: Mapped[str | None] = mapped_column(
        ForeignKey("prospects.id"), nullable=True, index=True
    )
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    actor: Mapped[str] = mapped_column(String(80))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    previous_hash: Mapped[str] = mapped_column(String(64), default="0" * 64)
    content_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
