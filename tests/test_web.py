from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from fivefold.auth import SESSION_COOKIE, create_session, csrf_token
from fivefold.config import get_settings
from fivefold.contracts import ResearchRunRequest
from fivefold.models import PricingSetting, Prospect
from fivefold.web import app
from fivefold.workflow import create_research_run, run_fixture_to_completion


def test_api_requires_admin_and_preview_is_safe(db: Session) -> None:
    async def scenario() -> None:
        await create_research_run(
            db,
            get_settings(),
            ResearchRunRequest(provider="fixture", max_businesses=1),
        )
        await run_fixture_to_completion(db, get_settings())

    asyncio.run(scenario())
    prospect = db.scalar(select(Prospect))
    assert prospect and prospect.preview_path

    with TestClient(app) as client:
        assert client.get("/api/prospects").status_code == 401
        client.cookies.set(SESSION_COOKIE, create_session(get_settings()))
        assert client.get("/api/prospects").status_code == 200
        response = client.get(prospect.preview_path)
        assert response.status_code == 200
        assert "Independent concept preview" in response.text
        assert "<form" not in response.text.lower()
        assert "noindex" in response.headers["x-robots-tag"]
        assert "form-action 'none'" in response.headers["content-security-policy"]


def test_human_status_is_only_changed_by_authenticated_form(db: Session) -> None:
    async def create_only() -> None:
        await create_research_run(
            db,
            get_settings(),
            ResearchRunRequest(provider="fixture", max_businesses=1),
        )

    asyncio.run(create_only())
    prospect = db.scalar(select(Prospect))
    assert prospect
    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE, create_session(get_settings()))
        response = client.post(
            f"/api/prospects/{prospect.id}/human-status",
            data={"status": "verified", "note": "Checked by operator", "csrf": csrf_token(get_settings())},
            follow_redirects=False,
        )
        assert response.status_code == 303
    db.refresh(prospect)
    assert prospect.human_status == "verified"


def test_no_outreach_or_domain_purchase_route_exists() -> None:
    paths = {route.path.lower() for route in app.routes}
    forbidden = ("send-email", "send-message", "purchase-domain", "register-domain")
    assert all(all(term not in path for term in forbidden) for path in paths)


def test_pricing_updates_are_versioned_and_artifacts_export_lineage(db: Session) -> None:
    async def scenario() -> None:
        await create_research_run(
            db,
            get_settings(),
            ResearchRunRequest(provider="fixture", max_businesses=1),
        )
        await run_fixture_to_completion(db, get_settings())

    asyncio.run(scenario())
    prospect = db.scalar(select(Prospect))
    assert prospect is not None
    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE, create_session(get_settings()))
        exported = client.get(f"/api/prospects/{prospect.id}/artifacts")
        assert exported.status_code == 200
        artifacts = exported.json()["artifacts"]
        assert len(artifacts) == 5
        manager_export = next(item for item in artifacts if item["stage"] == "manager")
        assert manager_export["input_artifact_ids"]
        response = client.post(
            "/api/settings/pricing",
            data={
                "monthly_eur": "14.99",
                "annual_eur": "149.99",
                "three_year_eur": "439.99",
                "domain_annual_eur": "23.99",
                "platform_allocation_annual_eur": "24",
                "data_api_allocation_annual_eur": "12",
                "operations_reserve_annual_eur": "18",
                "early_cancellation_risk_eur": "38",
                "source_note": "Test version",
                "csrf": csrf_token(get_settings()),
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
    db.expire_all()
    versions = db.scalars(select(PricingSetting)).all()
    assert any(item.values["source_note"] == "Test version" for item in versions)
