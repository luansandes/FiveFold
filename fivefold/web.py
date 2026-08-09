from __future__ import annotations

import hashlib
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc, func, or_, select, update
from sqlalchemy.orm import Session

from fivefold.agent_runtime import validate_agent_output_schemas
from fivefold.audit import append_audit, verify_audit_chain
from fivefold.auth import (
    SESSION_COOKIE,
    check_login_rate_limit,
    create_session,
    csrf_token,
    read_session,
    require_admin,
    verify_csrf,
    verify_password,
)
from fivefold.config import Settings, get_settings
from fivefold.contracts import HumanStatusRequest, ResearchRunRequest, Stage, WebsiteArtifact
from fivefold.db import get_db, get_engine, get_session_factory, init_db
from fivefold.models import (
    Artifact,
    AuditEvent,
    Handoff,
    Job,
    OperationalSetting,
    PreviewToken,
    PricingSetting,
    Prospect,
    ResearchRun,
    StageRun,
    SystemMarker,
    utcnow,
)
from fivefold.operational import current_operational_setting
from fivefold.reset import reset_generated_data
from fivefold.site_builder import render_public_preview
from fivefold.workflow import (
    create_research_run,
    enqueue_job,
    latest_artifact,
    process_job_by_id,
    recover_queued_jobs,
)

ROOT = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(ROOT / "templates"))
DbSession = Annotated[Session, Depends(get_db)]

DISCLOSURE = (
    "Independent concept preview created by Fivefold Web. "
    "This is not the business’s official website and is not affiliated with or approved "
    "by the business. Do not submit personal information."
)


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
    settings = get_settings()
    missing_connections = [
        name
        for name, value in (
            ("OPENAI_API_KEY", settings.openai_api_key),
            ("GOOGLE_MAPS_API_KEY", settings.google_maps_api_key),
            ("ADMIN_PASSWORD_HASH", settings.admin_password_hash),
        )
        if not value
    ]
    if missing_connections:
        raise RuntimeError(
            "Live configuration is required: " + ", ".join(missing_connections)
        )
    if settings.is_production:
        defaults = [
            settings.session_secret.startswith("development-"),
            settings.cron_secret.startswith("development-"),
            settings.preview_signing_secret.startswith("development-"),
            not settings.admin_password_hash,
        ]
        if any(defaults):
            raise RuntimeError("Production secrets and ADMIN_PASSWORD_HASH must be configured")
    init_db()
    validate_agent_output_schemas()
    yield


app = FastAPI(
    title="Fivefold Web",
    description="Exactly five specialised agents with auditable prospect handoffs.",
    version="0.1.0",
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=str(ROOT / "static")), name="static")


def settings() -> Settings:
    return get_settings()


def assert_admin(request: Request) -> Settings:
    config = settings()
    require_admin(request, config)
    return config


def assert_csrf(request: Request, token: str | None) -> None:
    if not verify_csrf(settings(), token):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")


def prospect_summary(prospect: Prospect) -> dict[str, Any]:
    return {
        "id": prospect.id,
        "business_name": prospect.business_name,
        "category": prospect.category,
        "location": prospect.location,
        "footprint": prospect.footprint,
        "qualification_reason": prospect.qualification_reason,
        "current_stage": prospect.current_stage,
        "status": prospect.status,
        "priority": prospect.priority,
        "human_status": prospect.human_status,
        "preview_path": prospect.preview_path,
        "revision_count": prospect.revision_count,
        "updated_at": prospect.updated_at,
    }


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "agents": "5", "model": settings().openai_model}


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request) -> HTMLResponse:
    if read_session(settings(), request.cookies.get(SESSION_COOKIE)):
        return RedirectResponse("/", status_code=303)  # type: ignore[return-value]
    return templates.TemplateResponse(
        request,
        "login.html",
        {"csrf": csrf_token(settings(), "login"), "error": None},
    )


@app.post("/api/login")
def login(
    request: Request,
    password: Annotated[str, Form()],
    csrf: Annotated[str, Form()],
) -> Response:
    config = settings()
    client_key = request.client.host if request.client else "unknown"
    check_login_rate_limit(client_key)
    if not verify_csrf(config, csrf, "login") or not verify_password(
        password, config.admin_password_hash
    ):
        template_response = templates.TemplateResponse(
            request,
            "login.html",
            {"csrf": csrf_token(config, "login"), "error": "Invalid credentials."},
            status_code=401,
        )
        return template_response
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        SESSION_COOKIE,
        create_session(config),
        httponly=True,
        secure=config.is_production,
        samesite="strict",
        max_age=12 * 60 * 60,
    )
    return response


@app.post("/api/logout")
def logout(request: Request, csrf: Annotated[str, Form()]) -> RedirectResponse:
    assert_admin(request)
    assert_csrf(request, csrf)
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    return response


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: DbSession) -> HTMLResponse:
    if not read_session(settings(), request.cookies.get(SESSION_COOKIE)):
        return RedirectResponse("/login", status_code=303)  # type: ignore[return-value]
    prospects = db.scalars(select(Prospect).order_by(desc(Prospect.updated_at))).all()
    counts = {
        "total": len(prospects),
        "curated": sum(item.status == "curated" for item in prospects),
        "running": sum(item.status in {"queued", "running"} for item in prospects),
        "review": sum(item.status == "needs_human_review" for item in prospects),
        "failed": sum(item.status == "failed" for item in prospects),
        "ready_for_outreach": sum(
            item.status == "curated" and item.human_status in {"unverified", "verified"}
            for item in prospects
        ),
    }
    stage_counts = {
        stage.value: sum(item.current_stage == stage.value for item in prospects)
        for stage in Stage
    }
    recent_handoffs = db.execute(
        select(Handoff, Prospect.business_name)
        .join(Prospect, Prospect.id == Handoff.prospect_id)
        .order_by(desc(Handoff.created_at))
        .limit(8)
    ).all()
    recent_runs = db.scalars(
        select(ResearchRun).order_by(desc(ResearchRun.created_at)).limit(5)
    ).all()
    stage_run_counts = {
        "completed": db.scalar(
            select(func.count()).select_from(StageRun).where(StageRun.status == "completed")
        )
        or 0,
        "failed": db.scalar(
            select(func.count()).select_from(StageRun).where(StageRun.status == "failed")
        )
        or 0,
    }
    operational = current_operational_setting(db)
    stalled_before = utcnow() - timedelta(minutes=2)
    queue_counts = {
        state: db.scalar(select(func.count()).select_from(Job).where(Job.status == state)) or 0
        for state in ("queued", "running", "failed", "publish_failed")
    }
    queue_counts["stalled"] = (
        db.scalar(
            select(func.count())
            .select_from(Job)
            .where(Job.status == "queued", Job.created_at <= stalled_before)
        )
        or 0
    )
    if queue_counts["publish_failed"]:
        queue_health = {
            "state": "failed",
            "label": "Queue publication failed",
            "recoverable": True,
        }
    elif queue_counts["stalled"]:
        queue_health = {
            "state": "stalled",
            "label": f"{queue_counts['stalled']} stalled job(s)",
            "recoverable": True,
        }
    elif queue_counts["running"] or queue_counts["queued"]:
        queue_health = {
            "state": "processing",
            "label": "Automatic processing active",
            "recoverable": False,
        }
    else:
        queue_health = {
            "state": "healthy",
            "label": "Queue healthy",
            "recoverable": False,
        }
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "prospects": prospects,
            "counts": counts,
            "stage_counts": stage_counts,
            "stage_run_counts": stage_run_counts,
            "recent_handoffs": recent_handoffs,
            "recent_runs": recent_runs,
            "stages": list(Stage),
            "csrf": csrf_token(settings()),
            "operational": operational,
            "queue_counts": queue_counts,
            "queue_health": queue_health,
            "authenticated": True,
            "active_page": "dashboard",
        },
    )


@app.get("/prospects", response_class=HTMLResponse)
def prospects_page(request: Request, db: DbSession) -> HTMLResponse:
    if not read_session(settings(), request.cookies.get(SESSION_COOKIE)):
        return RedirectResponse("/login", status_code=303)  # type: ignore[return-value]
    search = request.query_params.get("q", "").strip()
    status = request.query_params.get("status", "").strip()
    stage = request.query_params.get("stage", "").strip()
    query = select(Prospect)
    if search:
        pattern = f"%{search}%"
        query = query.where(
            or_(
                Prospect.business_name.ilike(pattern),
                Prospect.category.ilike(pattern),
                Prospect.location.ilike(pattern),
            )
        )
    if status:
        query = query.where(Prospect.status == status)
    if stage:
        query = query.where(Prospect.current_stage == stage)
    prospects = db.scalars(query.order_by(desc(Prospect.updated_at))).all()
    return templates.TemplateResponse(
        request,
        "prospects.html",
        {
            "prospects": prospects,
            "stages": list(Stage),
            "filters": {"q": search, "status": status, "stage": stage},
            "csrf": csrf_token(settings()),
            "authenticated": True,
            "active_page": "prospects",
        },
    )


@app.get("/prospects/{prospect_id}", response_class=HTMLResponse)
def prospect_page(request: Request, prospect_id: str, db: DbSession) -> HTMLResponse:
    if not read_session(settings(), request.cookies.get(SESSION_COOKIE)):
        return RedirectResponse("/login", status_code=303)  # type: ignore[return-value]
    prospect = db.get(Prospect, prospect_id)
    if not prospect:
        raise HTTPException(status_code=404, detail="Prospect not found")
    artifacts = db.scalars(
        select(Artifact)
        .where(Artifact.prospect_id == prospect_id)
        .order_by(Artifact.created_at, Artifact.version)
    ).all()
    handoffs = db.scalars(
        select(Handoff)
        .where(Handoff.prospect_id == prospect_id)
        .order_by(Handoff.created_at)
    ).all()
    stage_runs = db.scalars(
        select(StageRun)
        .where(StageRun.prospect_id == prospect_id)
        .order_by(StageRun.started_at)
    ).all()
    events = db.scalars(
        select(AuditEvent)
        .where(AuditEvent.prospect_id == prospect_id)
        .order_by(AuditEvent.created_at)
    ).all()
    latest = {
        stage.value: latest_artifact(db, prospect_id, stage)
        for stage in Stage
    }
    research_run = db.get(ResearchRun, prospect.research_run_id)
    return templates.TemplateResponse(
        request,
        "prospect.html",
        {
            "prospect": prospect,
            "artifacts": artifacts,
            "handoffs": handoffs,
            "stage_runs": stage_runs,
            "events": events,
            "audit_valid": verify_audit_chain(list(events)),
            "latest": latest,
            "research_run": research_run,
            "stages": list(Stage),
            "csrf": csrf_token(settings()),
            "base_url": settings().base_url.rstrip("/"),
            "authenticated": True,
            "active_page": "prospects",
        },
    )


@app.post("/api/research-runs")
async def start_research(
    request: Request,
    db: DbSession,
    location: Annotated[str, Form()] = "Dublin, Ireland",
    categories: Annotated[str, Form()] = "plumbers",
    csrf: Annotated[str, Form()] = "",
) -> RedirectResponse:
    config = assert_admin(request)
    assert_csrf(request, csrf)
    payload = ResearchRunRequest(
        location=location,
        categories=[item.strip() for item in categories.split(",") if item.strip()],
    )
    await create_research_run(db, config, payload)
    return RedirectResponse("/", status_code=303)


@app.get("/api/research-runs/{run_id}")
def get_research_run(request: Request, run_id: str, db: DbSession) -> dict[str, Any]:
    assert_admin(request)
    run = db.get(ResearchRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Research run not found")
    prospects = db.scalars(select(Prospect).where(Prospect.research_run_id == run_id)).all()
    return {
        "id": run.id,
        "status": run.status,
        "provider": run.provider,
        "prospects": [prospect_summary(item) for item in prospects],
    }


@app.get("/api/prospects")
def list_prospects(request: Request, db: DbSession) -> list[dict[str, Any]]:
    assert_admin(request)
    return [
        prospect_summary(item)
        for item in db.scalars(select(Prospect).order_by(desc(Prospect.updated_at))).all()
    ]


@app.get("/api/prospects/{prospect_id}")
def get_prospect(request: Request, prospect_id: str, db: DbSession) -> dict[str, Any]:
    assert_admin(request)
    prospect = db.get(Prospect, prospect_id)
    if not prospect:
        raise HTTPException(status_code=404, detail="Prospect not found")
    result = prospect_summary(prospect)
    result["artifacts"] = {
        stage.value: artifact.payload if (artifact := latest_artifact(db, prospect_id, stage)) else None
        for stage in Stage
    }
    return result


@app.post("/api/admin/queue/recover")
async def recover_stalled_queue(
    request: Request,
    db: DbSession,
    csrf: Annotated[str, Form()],
) -> RedirectResponse:
    config = assert_admin(request)
    assert_csrf(request, csrf)
    from fivefold.queueing import QueuePublishError

    try:
        job_ids = await recover_queued_jobs(db, config, 10)
    except QueuePublishError:
        return RedirectResponse("/?queue_recovery=failed", status_code=303)
    return RedirectResponse(
        f"/?queue_recovery=completed&republished={len(job_ids)}", status_code=303
    )


@app.post("/api/internal/jobs/{job_id}/process")
async def process_queued_job(request: Request, job_id: str, db: DbSession) -> JSONResponse:
    config = settings()
    if request.headers.get("authorization") != f"Bearer {config.cron_secret}":
        raise HTTPException(status_code=401, detail="Invalid worker secret")
    try:
        result = await process_job_by_id(db, config, job_id)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return JSONResponse(result)


@app.get("/api/internal/reset-generated")
def reset_generated_records(request: Request) -> JSONResponse:
    config = settings()
    if request.headers.get("authorization") != f"Bearer {config.cron_secret}":
        raise HTTPException(status_code=401, detail="Invalid maintenance secret")
    marker_key = "fresh-start-2026-08-09"
    engine = get_engine()
    marker_session = get_session_factory()()
    try:
        marker = marker_session.get(SystemMarker, marker_key)
        if marker:
            print(f"maintenance reset already completed: {marker_key}")
            return JSONResponse({"status": "already_completed", "marker": marker_key})
    finally:
        marker_session.close()
    result = reset_generated_data(engine)
    init_db()
    marker_session = get_session_factory()()
    try:
        marker_session.add(SystemMarker(key=marker_key, payload=result))
        marker_session.commit()
    finally:
        marker_session.close()
    print(f"maintenance reset completed: {result}")
    return JSONResponse({"status": "completed", "marker": marker_key, **result})


@app.post("/api/prospects/{prospect_id}/retry")
async def retry_prospect(
    request: Request,
    prospect_id: str,
    db: DbSession,
    csrf: Annotated[str, Form()],
) -> RedirectResponse:
    assert_admin(request)
    assert_csrf(request, csrf)
    prospect = db.get(Prospect, prospect_id)
    if not prospect:
        raise HTTPException(status_code=404, detail="Prospect not found")
    prospect.revision_count += 1
    prospect.status = "queued"
    job = enqueue_job(db, prospect, Stage(prospect.current_stage))
    append_audit(db, "prospect.retry_requested", "admin", {}, prospect.id)
    db.commit()
    from fivefold.queueing import QueuePublishError, publish_jobs

    try:
        await publish_jobs(settings(), [job.id])
        append_audit(
            db,
            "queue.published",
            "admin",
            {"job_ids": [job.id], "source": "prospect_retry"},
            prospect.id,
        )
    except QueuePublishError as exc:
        job.status = "publish_failed"
        job.last_error = str(exc)
        append_audit(
            db,
            "queue.publish_failed",
            "admin",
            {"job_ids": [job.id], "error": str(exc)},
            prospect.id,
        )
    db.commit()
    return RedirectResponse(f"/prospects/{prospect_id}", status_code=303)


@app.post("/api/prospects/{prospect_id}/human-status")
def update_human_status(
    request: Request,
    prospect_id: str,
    db: DbSession,
    status: Annotated[str, Form()],
    note: Annotated[str, Form()] = "",
    csrf: Annotated[str, Form()] = "",
) -> RedirectResponse:
    assert_admin(request)
    assert_csrf(request, csrf)
    payload = HumanStatusRequest(status=status, note=note)
    prospect = db.get(Prospect, prospect_id)
    if not prospect:
        raise HTTPException(status_code=404, detail="Prospect not found")
    prospect.human_status = payload.status
    prospect.human_note = payload.note
    append_audit(
        db,
        "human.status_changed",
        "admin",
        payload.model_dump(mode="json"),
        prospect.id,
    )
    db.commit()
    return RedirectResponse(f"/prospects/{prospect_id}", status_code=303)


@app.post("/api/prospects/{prospect_id}/revoke-preview")
def revoke_preview(
    request: Request,
    prospect_id: str,
    db: DbSession,
    csrf: Annotated[str, Form()],
) -> RedirectResponse:
    assert_admin(request)
    assert_csrf(request, csrf)
    db.execute(
        update(PreviewToken)
        .where(PreviewToken.prospect_id == prospect_id, PreviewToken.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )
    append_audit(db, "preview.revoked", "admin", {}, prospect_id)
    db.commit()
    return RedirectResponse(f"/prospects/{prospect_id}", status_code=303)


@app.get("/api/prospects/{prospect_id}/audit")
def export_audit(request: Request, prospect_id: str, db: DbSession) -> JSONResponse:
    assert_admin(request)
    events = db.scalars(
        select(AuditEvent)
        .where(AuditEvent.prospect_id == prospect_id)
        .order_by(AuditEvent.created_at)
    ).all()
    return JSONResponse(
        {
            "prospect_id": prospect_id,
            "chain_valid": verify_audit_chain(list(events)),
            "events": [
                {
                    "id": event.id,
                    "type": event.event_type,
                    "actor": event.actor,
                    "payload": event.payload,
                    "previous_hash": event.previous_hash,
                    "hash": event.content_hash,
                    "created_at": event.created_at.isoformat(),
                }
                for event in events
            ],
        }
    )


@app.get("/api/prospects/{prospect_id}/artifacts")
def export_artifacts(request: Request, prospect_id: str, db: DbSession) -> JSONResponse:
    assert_admin(request)
    prospect = db.get(Prospect, prospect_id)
    if not prospect:
        raise HTTPException(status_code=404, detail="Prospect not found")
    records = db.scalars(
        select(Artifact)
        .where(Artifact.prospect_id == prospect_id)
        .order_by(Artifact.stage, Artifact.version)
    ).all()
    producer_runs = {
        run.output_artifact_id: run
        for run in db.scalars(
            select(StageRun).where(StageRun.prospect_id == prospect_id)
        ).all()
        if run.output_artifact_id
    }
    return JSONResponse(
        {
            "prospect_id": prospect_id,
            "artifacts": [
                {
                    "stage": record.stage,
                    "version": record.version,
                    "stale": record.stale,
                    "content_hash": record.content_hash,
                    "input_artifact_ids": producer_runs[record.id].input_artifact_ids
                    if record.id in producer_runs
                    else [],
                    "payload": record.payload,
                    "created_at": record.created_at.isoformat(),
                }
                for record in records
            ],
        }
    )


@app.get("/settings", response_class=HTMLResponse)
def pricing_page(request: Request, db: DbSession) -> Response:
    if not read_session(settings(), request.cookies.get(SESSION_COOKIE)):
        return RedirectResponse("/login", status_code=303)
    current = db.scalar(select(PricingSetting).order_by(desc(PricingSetting.effective_at)))
    operational = current_operational_setting(db)
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "pricing": current,
            "operational": operational,
            "csrf": csrf_token(settings()),
            "authenticated": True,
            "active_page": "settings",
        },
    )


@app.get("/api/settings/pipeline")
def get_pipeline_settings(request: Request, db: DbSession) -> dict[str, Any]:
    assert_admin(request)
    current = current_operational_setting(db)
    return {
        "id": current.id,
        "max_prospects_per_run": current.max_prospects_per_run,
        "opportunity_score_threshold": current.opportunity_score_threshold,
        "effective_at": current.effective_at,
    }


@app.post("/api/settings/pipeline")
def update_pipeline_settings(
    request: Request,
    db: DbSession,
    max_prospects_per_run: Annotated[int, Form()],
    opportunity_score_threshold: Annotated[int, Form()],
    csrf: Annotated[str, Form()] = "",
) -> RedirectResponse:
    assert_admin(request)
    assert_csrf(request, csrf)
    if not 1 <= max_prospects_per_run <= 10:
        raise HTTPException(status_code=422, detail="Prospect limit must be between 1 and 10")
    if not 0 <= opportunity_score_threshold <= 100:
        raise HTTPException(status_code=422, detail="Opportunity threshold must be 0 to 100")
    record = OperationalSetting(
        name=f"operations-{utcnow().isoformat()}",
        max_prospects_per_run=max_prospects_per_run,
        opportunity_score_threshold=opportunity_score_threshold,
    )
    db.add(record)
    db.flush()
    append_audit(
        db,
        "operations.version_created",
        "admin",
        {
            "operational_setting_id": record.id,
            "max_prospects_per_run": max_prospects_per_run,
            "opportunity_score_threshold": opportunity_score_threshold,
        },
    )
    db.commit()
    return RedirectResponse("/settings", status_code=303)


@app.get("/api/settings/pricing")
def get_pricing(request: Request, db: DbSession) -> dict[str, Any]:
    assert_admin(request)
    current = db.scalar(select(PricingSetting).order_by(desc(PricingSetting.effective_at)))
    if not current:
        raise HTTPException(status_code=404, detail="Pricing settings not found")
    return {
        "id": current.id,
        "name": current.name,
        "values": current.values,
        "effective_at": current.effective_at,
    }


@app.post("/api/settings/pricing")
def update_pricing(
    request: Request,
    db: DbSession,
    monthly_eur: Annotated[float, Form()],
    annual_eur: Annotated[float, Form()],
    three_year_eur: Annotated[float, Form()],
    domain_annual_eur: Annotated[float, Form()],
    platform_allocation_annual_eur: Annotated[float, Form()],
    data_api_allocation_annual_eur: Annotated[float, Form()],
    operations_reserve_annual_eur: Annotated[float, Form()],
    early_cancellation_risk_eur: Annotated[float, Form()],
    source_note: Annotated[str, Form()] = "Operator update",
    csrf: Annotated[str, Form()] = "",
) -> RedirectResponse:
    assert_admin(request)
    assert_csrf(request, csrf)
    numeric_values = {
        "monthly_eur": monthly_eur,
        "annual_eur": annual_eur,
        "three_year_eur": three_year_eur,
        "domain_annual_eur": domain_annual_eur,
        "platform_allocation_annual_eur": platform_allocation_annual_eur,
        "data_api_allocation_annual_eur": data_api_allocation_annual_eur,
        "operations_reserve_annual_eur": operations_reserve_annual_eur,
        "early_cancellation_risk_eur": early_cancellation_risk_eur,
    }
    if any(value < 0 for value in numeric_values.values()):
        raise HTTPException(status_code=422, detail="Pricing values cannot be negative")
    values: dict[str, Any] = {
        **numeric_values,
        "vat_rate": 0.23,
        "source_note": source_note.strip() or "Operator update",
        "checked_at": utcnow().isoformat(),
    }
    record = PricingSetting(name=f"pricing-{utcnow().isoformat()}", values=values)
    db.add(record)
    db.flush()
    append_audit(db, "pricing.version_created", "admin", {"pricing_id": record.id, **values})
    db.commit()
    return RedirectResponse("/settings", status_code=303)


@app.post("/api/prospects/{prospect_id}/redact")
def redact_prospect(
    request: Request,
    prospect_id: str,
    db: DbSession,
    csrf: Annotated[str, Form()],
) -> RedirectResponse:
    assert_admin(request)
    assert_csrf(request, csrf)
    prospect = db.get(Prospect, prospect_id)
    if not prospect:
        raise HTTPException(status_code=404, detail="Prospect not found")
    prospect.business_name = "Redacted prospect"
    prospect.website_url = None
    prospect.place_id = None
    prospect.human_note = ""
    prospect.redacted = True
    prospect.preview_path = None
    db.execute(
        update(PreviewToken)
        .where(PreviewToken.prospect_id == prospect_id, PreviewToken.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )
    append_audit(
        db,
        "prospect.redacted",
        "admin",
        {"note": "Direct identifiers removed; audit metadata retained for accountability."},
        prospect_id,
    )
    db.commit()
    return RedirectResponse(f"/prospects/{prospect_id}", status_code=303)


@app.get("/preview/{token}", response_class=HTMLResponse)
def public_preview(token: str, db: DbSession) -> HTMLResponse:
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    preview = db.scalar(select(PreviewToken).where(PreviewToken.token_hash == token_hash))
    now = datetime.now(UTC)
    if not preview or preview.revoked_at is not None:
        raise HTTPException(status_code=404, detail="Preview not found")
    expires = preview.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    if expires <= now:
        raise HTTPException(status_code=410, detail="Preview expired")
    artifact_record = db.get(Artifact, preview.artifact_id)
    if not artifact_record or artifact_record.stale:
        raise HTTPException(status_code=410, detail="Preview superseded")
    artifact = WebsiteArtifact.model_validate(artifact_record.payload["artifact"])
    response = HTMLResponse(render_public_preview(artifact, DISCLOSURE))
    response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; style-src 'unsafe-inline'; img-src data:; "
        "font-src 'none'; form-action 'none'; frame-ancestors 'self'; base-uri 'none'"
    )
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@app.get("/privacy", response_class=HTMLResponse)
def privacy(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "legal.html", {"title": "Privacy", "content": "Fivefold Web stores prospect research and audit records for internal evaluation. Contact details must be independently verified before human outreach. Access, retention, and redaction are controlled by the administrator. Google Places data is refreshed and attributed according to applicable platform terms."})


@app.get("/terms", response_class=HTMLResponse)
def terms(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "legal.html", {"title": "Terms", "content": "Generated websites are independent concept previews, not official business websites. Fivefold Web agents cannot contact businesses, purchase domains, accept form submissions, or publish a client site. A human must review and approve all external actions."})
