from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from fivefold.config import get_settings
from fivefold.contracts import DecisionKind, HandoffDecision, ResearchRunRequest, Stage
from fivefold.models import Artifact, Handoff, Job, PipelineRun, Prospect, ResearchRun
from fivefold.workflow import _apply_handoff, create_research_run, latest_artifact

LIVE_CANDIDATE = {
    "business_name": "Live Business",
    "category": "Painter",
    "location": "Dublin, Ireland",
    "place_id": "google-place-id",
    "website_url": None,
    "footprint": "absent",
    "qualification_reason": "No owned website was found.",
    "review_themes": [],
    "audit": {"score": 5, "findings": ["No owned website URL was found."]},
    "opportunity_score": 95,
}


def test_research_always_uses_live_provider(db: Session) -> None:
    async def scenario() -> None:
        with patch(
            "fivefold.workflow.live_candidates",
            new=AsyncMock(return_value=([LIVE_CANDIDATE], 0)),
        ) as discover:
            run = await create_research_run(db, get_settings(), ResearchRunRequest())
        assert run.provider == "live"
        discover.assert_awaited_once()

    asyncio.run(scenario())
    prospect = db.scalar(select(Prospect))
    assert prospect is not None
    assert prospect.current_stage == Stage.RESEARCHER.value
    assert latest_artifact(db, prospect.id, Stage.MAKER) is None
    assert json.loads(prospect.human_note)["opportunity_score"] == 95


def test_provider_override_is_rejected() -> None:
    default_request = ResearchRunRequest()
    assert default_request.categories == ["plumbers"]
    with pytest.raises(ValidationError):
        ResearchRunRequest(provider="offline")


def test_duplicate_place_id_is_skipped_across_runs(db: Session) -> None:
    async def scenario() -> None:
        provider = AsyncMock(return_value=([LIVE_CANDIDATE], 0))
        with patch("fivefold.workflow.live_candidates", new=provider):
            first = await create_research_run(db, get_settings(), ResearchRunRequest())
            second = await create_research_run(db, get_settings(), ResearchRunRequest())
        assert first.created_count == 1
        assert second.created_count == 0
        assert second.duplicates_skipped == 1

    asyncio.run(scenario())
    assert len(db.scalars(select(Prospect)).all()) == 1


@pytest.mark.parametrize(
    ("score", "expected_status", "expected_handoff", "designer_queued"),
    [(90, "not_qualified", "filtered", False), (91, "queued", "advance", True)],
)
def test_opportunity_threshold_is_strictly_greater_than(
    db: Session,
    score: int,
    expected_status: str,
    expected_handoff: str,
    designer_queued: bool,
) -> None:
    run = ResearchRun(
        location="Dublin, Ireland",
        categories=["Painter"],
        max_businesses=1,
        opportunity_threshold=90,
        provider="live",
        status="completed",
    )
    db.add(run)
    db.flush()
    prospect = Prospect(
        research_run_id=run.id,
        business_name=f"Threshold {score}",
        category="Painter",
        place_id=f"place-{score}",
        footprint="absent",
        qualification_reason="No website",
    )
    db.add(prospect)
    db.flush()
    pipeline = PipelineRun(prospect_id=prospect.id, status="running")
    artifact = Artifact(
        prospect_id=prospect.id,
        stage="researcher",
        version=1,
        payload={"artifact": {"opportunity_score": score}},
        content_hash=str(score).zfill(64),
    )
    db.add_all([pipeline, artifact])
    db.flush()
    _apply_handoff(
        db,
        prospect,
        Stage.RESEARCHER,
        artifact,
        HandoffDecision(action=DecisionKind.ADVANCE, reason="Research complete"),
        pipeline,
    )
    db.commit()
    assert prospect.status == expected_status
    handoff = db.scalar(select(Handoff).where(Handoff.prospect_id == prospect.id))
    assert handoff is not None and handoff.decision == expected_handoff
    designer_job = db.scalar(
        select(Job).where(Job.prospect_id == prospect.id, Job.stage == "designer")
    )
    assert (designer_job is not None) is designer_queued
