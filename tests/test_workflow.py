from __future__ import annotations

import asyncio

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from fivefold.audit import verify_audit_chain
from fivefold.config import get_settings
from fivefold.contracts import ResearchRunRequest, Stage
from fivefold.models import Artifact, AuditEvent, Handoff, Prospect, StageRun
from fivefold.workflow import create_research_run, latest_artifact, run_fixture_to_completion


def test_fixture_pipeline_is_complete_auditable_and_revision_capable(db: Session) -> None:
    async def scenario() -> None:
        run = await create_research_run(
            db,
            get_settings(),
            ResearchRunRequest(provider="fixture", max_businesses=3),
        )
        results = await run_fixture_to_completion(db, get_settings())
        assert run.status == "completed"
        assert len(results) == 17

    asyncio.run(scenario())

    prospects = db.scalars(select(Prospect).order_by(Prospect.business_name)).all()
    assert len(prospects) == 3
    assert all(item.status == "curated" for item in prospects)
    assert all(item.preview_path for item in prospects)

    for prospect in prospects:
        for stage in Stage:
            artifact = latest_artifact(db, prospect.id, stage)
            assert artifact is not None, (prospect.business_name, stage)
            assert artifact.payload["artifact"]
        events = db.scalars(
            select(AuditEvent)
            .where(AuditEvent.prospect_id == prospect.id)
            .order_by(AuditEvent.created_at)
        ).all()
        assert verify_audit_chain(list(events))

    harbour = next(item for item in prospects if item.business_name == "Harbour Bloom Florals")
    correction = db.scalar(
        select(Handoff).where(
            Handoff.prospect_id == harbour.id,
            Handoff.decision == "revise_stage",
            Handoff.to_stage == "communicator",
        )
    )
    assert correction is not None
    assert harbour.revision_count == 1
    assert db.scalar(
        select(func.count(StageRun.id)).where(StageRun.prospect_id == harbour.id)
    ) == 7
    assert db.scalar(
        select(func.count(Artifact.id)).where(
            Artifact.prospect_id == harbour.id,
            Artifact.stale.is_(True),
        )
    ) >= 2
    communicator = latest_artifact(db, harbour.id, Stage.COMMUNICATOR)
    manager = latest_artifact(db, harbour.id, Stage.MANAGER)
    assert communicator is not None and manager is not None
    assert communicator.version == 2
    assert manager.payload["artifact"]["inherited_communication_version"] == 2
    assert harbour.preview_path in communicator.payload["artifact"]["email_draft"]
    manager_run = db.scalar(
        select(StageRun).where(StageRun.output_artifact_id == manager.id)
    )
    assert manager_run is not None
    assert communicator.id in manager_run.input_artifact_ids


def test_stage_order_cannot_skip_missing_input(db: Session) -> None:
    # The contract is represented by the fixed enum and latest-artifact lookup. A fresh
    # prospect has no Maker artefact until Researcher and Designer have completed.
    async def create_only() -> None:
        await create_research_run(
            db,
            get_settings(),
            ResearchRunRequest(provider="fixture", max_businesses=1),
        )

    asyncio.run(create_only())
    prospect = db.scalar(select(Prospect))
    assert prospect is not None
    assert latest_artifact(db, prospect.id, Stage.MAKER) is None
    assert prospect.current_stage == Stage.RESEARCHER.value
