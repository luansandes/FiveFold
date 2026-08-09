from __future__ import annotations

import hashlib
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from fivefold.auth import SESSION_COOKIE, create_session, csrf_token
from fivefold.config import get_settings
from fivefold.models import (
    Artifact,
    Handoff,
    PreviewToken,
    PricingSetting,
    Prospect,
    ResearchRun,
    StageRun,
    utcnow,
)
from fivefold.web import app


def add_live_prospect(db: Session) -> Prospect:
    run = ResearchRun(
        location="Dublin, Ireland",
        categories=["Painter"],
        max_businesses=1,
        provider="live",
        status="completed",
    )
    db.add(run)
    db.flush()
    prospect = Prospect(
        research_run_id=run.id,
        business_name="Live Business",
        category="Painter",
        location="Dublin, Ireland",
        place_id="google-place-id",
        footprint="absent",
        qualification_reason="No owned website was found.",
    )
    db.add(prospect)
    db.commit()
    return prospect


def test_api_requires_admin_and_preview_is_safe(db: Session) -> None:
    prospect = add_live_prospect(db)
    artifact_payload = {
        "title": "Live Business concept",
        "html": '<html><head><meta name="viewport"></head><body><main><h1>Live Business</h1></main></body></html>',
        "css": "@media(max-width:800px){main{display:block}}",
        "meta_description": "Independent concept",
        "structured_data": {
            "name": "Live Business",
            "description": "Independent concept",
            "area_served": "Dublin, Ireland",
        },
        "content_manifest": [],
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
            "warnings": [],
        },
        "artefact_hash": "a" * 64,
        "inherited_design_version": 1,
        "preview_path": None,
    }
    artifact = Artifact(
        prospect_id=prospect.id,
        stage="maker",
        version=1,
        payload={"artifact": artifact_payload},
        content_hash="b" * 64,
    )
    db.add(artifact)
    db.flush()
    token = "live-preview-token"
    db.add(
        PreviewToken(
            prospect_id=prospect.id,
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            artifact_id=artifact.id,
            expires_at=utcnow() + timedelta(days=1),
        )
    )
    db.commit()

    with TestClient(app) as client:
        assert client.get("/api/prospects").status_code == 401
        client.cookies.set(SESSION_COOKIE, create_session(get_settings()))
        assert client.get("/api/prospects").status_code == 200
        response = client.get(f"/preview/{token}")
        assert response.status_code == 200
        assert "Independent concept preview" in response.text
        assert "<form" not in response.text.lower()
        assert "noindex" in response.headers["x-robots-tag"]
        assert "form-action 'none'" in response.headers["content-security-policy"]


def test_human_status_is_only_changed_by_authenticated_form(db: Session) -> None:
    prospect = add_live_prospect(db)
    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE, create_session(get_settings()))
        response = client.post(
            f"/api/prospects/{prospect.id}/human-status",
            data={
                "status": "verified",
                "note": "Checked by operator",
                "csrf": csrf_token(get_settings()),
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
    db.refresh(prospect)
    assert prospect.human_status == "verified"


def test_no_demo_or_external_mutation_route_exists() -> None:
    paths = {route.path.lower() for route in app.routes}
    forbidden = ("demo", "send-email", "send-message", "purchase-domain", "register-domain")
    assert all(all(term not in path for term in forbidden) for path in paths)


def test_admin_ui_exposes_operational_handoffs_and_agent_runs(db: Session) -> None:
    prospect = add_live_prospect(db)
    run = StageRun(
        prospect_id=prospect.id,
        stage="researcher",
        status="completed",
        attempt=1,
        prompt_version="prompt-version-hash",
        input_artifact_ids=[],
        usage={"input_tokens": 120, "output_tokens": 80},
        completed_at=utcnow(),
    )
    handoff = Handoff(
        prospect_id=prospect.id,
        from_stage="researcher",
        to_stage="designer",
        decision="advance",
        reason="Evidence is sufficient for a tailored design.",
    )
    manager_artifact = Artifact(
        prospect_id=prospect.id,
        stage="manager",
        version=1,
        payload={
            "artifact": {
                "disposition": "accept",
                "priority": "high",
                "profitability": {
                    "annual_revenue_eur": 149.99,
                    "contribution_eur": 71.50,
                    "gross_margin_percent": 47.7,
                },
                "executive_summary": "Strong fit for a focused landing page.",
                "next_human_action": "Verify the public phone number before outreach.",
            }
        },
        content_hash="c" * 64,
    )
    db.add_all([run, handoff, manager_artifact])
    db.commit()

    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE, create_session(get_settings()))
        dashboard = client.get("/")
        assert dashboard.status_code == 200
        assert "Pipeline control" in dashboard.text
        assert "Recent handoffs" in dashboard.text
        assert "Researcher → Designer" in dashboard.text
        prospects_page = client.get("/prospects")
        assert prospects_page.status_code == 200
        assert "Pipeline records" in prospects_page.text

        detail = client.get(f"/prospects/{prospect.id}")
        assert detail.status_code == 200
        assert "Agent handoff log" in detail.text
        assert "Evidence is sufficient for a tailored design." in detail.text
        assert "Agent run history" in detail.text
        assert "200" in detail.text
        assert "ManagerDecision" in detail.text
        assert "Strong fit for a focused landing page." in detail.text


def test_pricing_updates_are_versioned(db: Session) -> None:
    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE, create_session(get_settings()))
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


def test_pipeline_settings_are_database_backed(db: Session) -> None:
    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE, create_session(get_settings()))
        initial = client.get("/api/settings/pipeline")
        assert initial.json()["max_prospects_per_run"] == 1
        assert initial.json()["opportunity_score_threshold"] == 90
        response = client.post(
            "/api/settings/pipeline",
            data={
                "max_prospects_per_run": "2",
                "opportunity_score_threshold": "91",
                "csrf": csrf_token(get_settings()),
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        updated = client.get("/api/settings/pipeline").json()
        assert updated["max_prospects_per_run"] == 2
        assert updated["opportunity_score_threshold"] == 91
