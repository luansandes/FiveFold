from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from fivefold.config import get_settings
from fivefold.contracts import ResearchRunRequest, Stage
from fivefold.models import Prospect
from fivefold.workflow import create_research_run, latest_artifact

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
            new=AsyncMock(return_value=[LIVE_CANDIDATE]),
        ) as discover:
            run = await create_research_run(db, get_settings(), ResearchRunRequest(max_businesses=1))
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
    assert default_request.max_businesses == 1
    with pytest.raises(ValidationError):
        ResearchRunRequest(provider="offline")
