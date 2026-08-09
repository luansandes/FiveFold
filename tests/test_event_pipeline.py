from __future__ import annotations

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock, patch

from sqlalchemy import select
from sqlalchemy.orm import Session

from fivefold.agent_runtime import AgentRuntime
from fivefold.config import get_settings
from fivefold.contracts import (
    CommunicationEnvelope,
    DesignEnvelope,
    MakerEnvelope,
    ManagerEnvelope,
    ResearchEnvelope,
    Stage,
)
from fivefold.models import AuditEvent, Job, PipelineRun, Prospect, ResearchRun
from fivefold.workflow import enqueue_job, process_job_by_id, recover_queued_jobs


def agent_outputs() -> list[object]:
    common = {
        "confidence": 0.95,
        "handoff": {"action": "advance", "reason": "Stage contract satisfied."},
    }
    return [
        ResearchEnvelope.model_validate(
            {
                **common,
                "artifact": {
                    "prospect_name": "Pipeline Business",
                    "category": "Painter",
                    "place_id": "pipeline-place",
                    "footprint": "absent",
                    "qualification_reason": "No owned website was found.",
                    "website_audit": {
                        "findings": ["No owned website URL was found."],
                        "score": 5,
                    },
                    "opportunity_score": 95,
                },
            }
        ),
        DesignEnvelope.model_validate(
            {
                **common,
                "artifact": {
                    "concept_name": "Local painter leads",
                    "audience": "Dublin homeowners",
                    "primary_goal": "Prompt quote calls",
                    "user_journey": ["Understand service", "Build trust", "Call"],
                    "sections": [
                        {
                            "section_type": "hero",
                            "heading": "Painting in Dublin",
                            "purpose": "Explain the offer",
                            "content_points": ["Local service", "Human-approved facts only"],
                        }
                    ],
                    "palette": {
                        "primary": "#17324d",
                        "secondary": "#315d7c",
                        "accent": "#ef8354",
                        "background": "#ffffff",
                        "text": "#111827",
                    },
                    "typography": {
                        "heading_family": "system-ui",
                        "body_family": "system-ui",
                        "base_size_px": 16,
                        "line_height": 1.5,
                    },
                    "primary_cta": "Call for a quote",
                    "trust_strategy": ["Verified service details"],
                    "accessibility_requirements": ["Keyboard accessible"],
                    "mobile_behaviour": ["Single column"],
                },
            }
        ),
        MakerEnvelope.model_validate(
            {
                **common,
                "artifact": {
                    "title": "Pipeline Business concept",
                    "html": '<html><head><meta name="viewport" content="width=device-width"></head><body><main><h1>Pipeline Business</h1></main></body></html>',
                    "css": "@media(max-width:800px){main{display:block}}",
                    "meta_description": "Independent painter website concept",
                    "structured_data": {
                        "name": "Pipeline Business",
                        "description": "Independent website concept",
                        "area_served": "Dublin, Ireland",
                    },
                    "content_manifest": ["Verified business name"],
                    "validation": {
                        "passed": True,
                        "checks": {
                            "no_scripts": True,
                            "no_iframes": True,
                            "no_active_forms": True,
                            "has_main": True,
                            "has_heading": True,
                            "has_viewport": True,
                            "responsive_css": True,
                            "no_javascript_urls": True,
                        },
                    },
                    "artefact_hash": "a" * 64,
                },
            }
        ),
        CommunicationEnvelope.model_validate(
            {
                **common,
                "artifact": {
                    "value_proposition": "An owned landing page for clearer enquiries.",
                    "email_subject": "Independent website concept",
                    "email_draft": "A human-reviewed draft with no automated sending.",
                    "call_outline": ["Verify identity", "Explain the concept"],
                    "follow_up_cadence": ["One respectful human follow-up"],
                    "objections": [
                        {"objection": "We use social media", "response": "The page complements it."}
                    ],
                    "preview_url": "https://example.test/preview/token",
                    "offer": {
                        "includes": ["One responsive landing page"],
                        "excludes": ["Ecommerce"],
                    },
                },
            }
        ),
        ManagerEnvelope.model_validate(
            {
                **common,
                "artifact": {
                    "disposition": "accept",
                    "priority": "high",
                    "quality_scores": {
                        "evidence_fidelity": 95,
                        "upstream_inheritance": 95,
                        "design_specificity": 92,
                        "preview_quality": 91,
                        "offer_consistency": 100,
                        "risk_compliance": 100,
                    },
                    "profitability": {
                        "annual_revenue_eur": 149.99,
                        "estimated_annual_cost_eur": 77.99,
                        "contribution_eur": 72.0,
                        "gross_margin_percent": 48.0,
                        "early_cancellation_risk_eur": 38.0,
                        "assumptions": ["Editable operating assumptions"],
                    },
                    "executive_summary": "Evidence and preview are aligned.",
                    "next_human_action": "Verify contact details before outreach.",
                },
            }
        ),
    ]


def test_event_driven_chain_processes_all_five_agents(db: Session) -> None:
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
        business_name="Pipeline Business",
        category="Painter",
        place_id="pipeline-place",
        footprint="absent",
        qualification_reason="No owned website was found.",
        human_note='{"opportunity_score":95,"audit":{"score":5},"review_themes":[]}',
    )
    db.add(prospect)
    db.flush()
    db.add(PipelineRun(prospect_id=prospect.id, status="queued"))
    first_job = enqueue_job(db, prospect, Stage.RESEARCHER)
    db.commit()

    async def scenario() -> None:
        current_job_id: str | None = first_job.id
        outputs = agent_outputs()
        with patch.object(AgentRuntime, "run", new=AsyncMock(side_effect=outputs)) as runtime:
            for expected_stage in Stage:
                assert current_job_id is not None
                result = await process_job_by_id(db, get_settings(), current_job_id)
                assert result["stage"] == expected_stage.value
                current_job_id = result["next_job_id"]
            assert current_job_id is None
            assert runtime.await_count == 5

    asyncio.run(scenario())
    db.refresh(prospect)
    assert prospect.status == "curated"
    assert len(db.scalars(select(Job).where(Job.prospect_id == prospect.id)).all()) == 5


def test_recovery_republishes_only_stalled_jobs_with_existing_ids(db: Session) -> None:
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
        business_name="Stalled Business",
        category="Painter",
        place_id="stalled-place",
        footprint="absent",
        qualification_reason="No owned website was found.",
    )
    db.add(prospect)
    db.flush()
    stalled = enqueue_job(db, prospect, Stage.RESEARCHER)
    stalled.created_at = stalled.created_at - timedelta(minutes=3)
    current = Job(
        prospect_id=prospect.id,
        stage=Stage.RESEARCHER.value,
        status="queued",
        idempotency_key="current-job",
    )
    failed = Job(
        prospect_id=prospect.id,
        stage=Stage.RESEARCHER.value,
        status="publish_failed",
        idempotency_key="publish-failed-job",
        last_error="publisher unavailable",
    )
    db.add_all([current, failed])
    db.commit()

    async def scenario() -> None:
        with patch("fivefold.workflow.publish_jobs", new=AsyncMock()) as publisher:
            recovered = await recover_queued_jobs(db, get_settings(), 10)
            assert recovered == [stalled.id, failed.id]
            publisher.assert_awaited_once_with(get_settings(), [stalled.id, failed.id])

    asyncio.run(scenario())
    db.refresh(failed)
    assert failed.status == "queued"
    assert failed.last_error is None
    assert db.scalar(
        select(AuditEvent).where(AuditEvent.event_type == "queue.recovered")
    ) is not None
