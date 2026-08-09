from __future__ import annotations

import hashlib
import secrets
from copy import deepcopy
from datetime import timedelta
from typing import Any

from sqlalchemy import desc, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from fivefold.agent_runtime import AgentRuntime
from fivefold.audit import append_audit, canonical_json, content_hash
from fivefold.config import Settings
from fivefold.contracts import STAGE_ORDER, DecisionKind, ResearchRunRequest, Stage
from fivefold.models import (
    Artifact,
    Handoff,
    Job,
    PipelineRun,
    PreviewToken,
    PricingSetting,
    PromptVersion,
    Prospect,
    ResearchRun,
    StageRun,
    utcnow,
)
from fivefold.operational import current_operational_setting
from fivefold.prompts import system_prompt
from fivefold.queueing import QueuePublishError, publish_jobs
from fivefold.research import live_candidates


class WorkflowError(RuntimeError):
    pass


def stage_index(stage: Stage | str) -> int:
    value = Stage(stage)
    return STAGE_ORDER.index(value)


def next_stage(stage: Stage) -> Stage | None:
    index = stage_index(stage)
    return STAGE_ORDER[index + 1] if index + 1 < len(STAGE_ORDER) else None


def prompt_version(session: Session, stage: Stage) -> str:
    prompt = system_prompt(stage)
    digest = hashlib.sha256(prompt.encode()).hexdigest()
    existing = session.scalar(select(PromptVersion).where(PromptVersion.version_hash == digest))
    if not existing:
        session.add(PromptVersion(stage=stage.value, version_hash=digest, prompt=prompt))
        session.flush()
    return digest


def latest_artifact(session: Session, prospect_id: str, stage: Stage) -> Artifact | None:
    return session.scalar(
        select(Artifact)
        .where(
            Artifact.prospect_id == prospect_id,
            Artifact.stage == stage.value,
            Artifact.stale.is_(False),
        )
        .order_by(desc(Artifact.version))
        .limit(1)
    )


def stage_inputs(session: Session, prospect_id: str, stage: Stage) -> dict[str, dict[str, Any]]:
    if stage == Stage.RESEARCHER:
        return {}
    required = STAGE_ORDER[: stage_index(stage)]
    result: dict[str, dict[str, Any]] = {}
    for required_stage in required:
        artifact = latest_artifact(session, prospect_id, required_stage)
        if artifact is None:
            raise WorkflowError(f"Missing required {required_stage.value} artefact")
        result[required_stage.value] = artifact.payload
    return result


def enqueue_job(session: Session, prospect: Prospect, stage: Stage) -> Job:
    key = f"{prospect.id}:{stage.value}:revision:{prospect.revision_count}"
    existing = session.scalar(select(Job).where(Job.idempotency_key == key))
    if existing:
        return existing
    job = Job(
        prospect_id=prospect.id,
        stage=stage.value,
        idempotency_key=key,
        status="queued",
    )
    session.add(job)
    session.flush()
    append_audit(
        session,
        "job.queued",
        "workflow",
        {"job_id": job.id, "stage": stage.value, "idempotency_key": key},
        prospect.id,
    )
    return job


async def create_research_run(
    session: Session, settings: Settings, request: ResearchRunRequest
) -> ResearchRun:
    operational = current_operational_setting(session)
    max_businesses = min(operational.max_prospects_per_run, 10)
    run = ResearchRun(
        location=request.location,
        categories=request.categories,
        max_businesses=max_businesses,
        operational_setting_id=operational.id,
        opportunity_threshold=operational.opportunity_score_threshold,
        provider="live",
        status="discovering",
    )
    session.add(run)
    session.flush()
    append_audit(
        session,
        "research_run.created",
        "admin",
        request.model_dump(mode="json"),
    )
    session.commit()

    try:
        existing_place_ids = {
            place_id
            for place_id in session.scalars(
                select(Prospect.place_id).where(Prospect.place_id.is_not(None))
            ).all()
            if place_id is not None
        }
        candidates, duplicates_skipped = await live_candidates(
            settings,
            request.location,
            request.categories,
            max_businesses,
            existing_place_ids,
        )
        run.duplicates_skipped = duplicates_skipped
        queued_job_ids: list[str] = []
        for candidate in candidates:
            try:
                with session.begin_nested():
                    prospect = Prospect(
                        research_run_id=run.id,
                        business_name=candidate["business_name"],
                        category=candidate["category"],
                        location=candidate["location"],
                        place_id=candidate.get("place_id"),
                        website_url=candidate.get("website_url"),
                        footprint=candidate["footprint"],
                        qualification_reason=candidate["qualification_reason"],
                        current_stage=Stage.RESEARCHER.value,
                        status="queued",
                    )
                    # Retain bounded qualification facts, never the raw Places response.
                    prospect.human_note = canonical_json(
                        {
                            "audit": candidate["audit"],
                            "review_themes": candidate["review_themes"],
                            "opportunity_score": candidate["opportunity_score"],
                        }
                    )
                    session.add(prospect)
                    session.flush()
                    session.add(PipelineRun(prospect_id=prospect.id, status="queued"))
                    append_audit(
                        session,
                        "prospect.discovered",
                        "Researcher",
                        {
                            "name": prospect.business_name,
                            "category": prospect.category,
                            "footprint": prospect.footprint,
                            "place_id": prospect.place_id,
                            "provider": "live",
                        },
                        prospect.id,
                    )
                    queued_job_ids.append(enqueue_job(session, prospect, Stage.RESEARCHER).id)
                    run.created_count += 1
            except IntegrityError:
                run.duplicates_skipped += 1
        run.status = "completed"
        run.completed_at = utcnow()
        session.commit()
        try:
            await publish_jobs(settings, queued_job_ids)
            append_audit(
                session,
                "queue.published",
                "workflow",
                {"job_ids": queued_job_ids, "source": "research_run"},
            )
            session.commit()
        except QueuePublishError as exc:
            session.execute(
                update(Job)
                .where(Job.id.in_(queued_job_ids), Job.status == "queued")
                .values(status="publish_failed", last_error=str(exc))
            )
            append_audit(
                session,
                "queue.publish_failed",
                "workflow",
                {"job_ids": queued_job_ids, "error": str(exc)},
            )
            session.commit()
        return run
    except Exception as exc:
        run.status = "failed"
        run.error = str(exc)
        session.commit()
        raise


def prospect_context(session: Session, prospect: Prospect) -> dict[str, Any]:
    research_data: dict[str, Any] = {}
    if prospect.human_note.startswith("{"):
        import json

        try:
            research_data = json.loads(prospect.human_note)
        except ValueError:
            research_data = {}
    pricing = session.scalar(select(PricingSetting).order_by(desc(PricingSetting.effective_at)).limit(1))
    return {
        "id": prospect.id,
        "business_name": prospect.business_name,
        "category": prospect.category,
        "location": prospect.location,
        "place_id": prospect.place_id,
        "website_url": prospect.website_url,
        "footprint": prospect.footprint,
        "qualification_reason": prospect.qualification_reason,
        "audit": research_data.get("audit", {"score": 0, "findings": []}),
        "review_themes": research_data.get("review_themes", []),
        "opportunity_score": research_data.get("opportunity_score", 70),
        "preview_path": prospect.preview_path,
        "pricing": pricing.values if pricing else {},
    }


def latest_revision_feedback(session: Session, prospect_id: str, stage: Stage) -> str | None:
    handoff = session.scalar(
        select(Handoff)
        .where(Handoff.prospect_id == prospect_id, Handoff.to_stage == stage.value)
        .order_by(desc(Handoff.created_at))
        .limit(1)
    )
    return handoff.reason if handoff and handoff.decision.startswith("revise") else None


def claim_job(session: Session, job_id: str | None = None) -> Job | None:
    query = select(Job).where(Job.status == "queued", Job.available_at <= utcnow())
    if job_id:
        query = query.where(Job.id == job_id)
    job = session.scalar(
        query
        .order_by(Job.created_at)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if not job:
        return None
    job.status = "running"
    job.locked_at = utcnow()
    job.attempt += 1
    session.commit()
    return job


async def process_job(session: Session, settings: Settings, job: Job) -> dict[str, Any]:
    prospect = session.get(Prospect, job.prospect_id)
    if not prospect:
        job.status = "failed"
        job.last_error = "Prospect not found"
        session.commit()
        raise WorkflowError("Prospect not found")
    stage = Stage(job.stage)
    if prospect.current_stage != stage.value:
        job.status = "cancelled"
        append_audit(
            session,
            "job.cancelled",
            "workflow",
            {"reason": "stale stage", "job_stage": stage.value, "current_stage": prospect.current_stage},
            prospect.id,
        )
        session.commit()
        return {"job_id": job.id, "status": "cancelled"}

    pipeline = session.scalar(select(PipelineRun).where(PipelineRun.prospect_id == prospect.id))
    if pipeline and not pipeline.started_at:
        pipeline.started_at = utcnow()
        pipeline.status = "running"
    version_hash = prompt_version(session, stage)
    inputs = stage_inputs(session, prospect.id, stage)
    attempt = (
        session.scalar(
            select(func.count(StageRun.id)).where(
                StageRun.prospect_id == prospect.id, StageRun.stage == stage.value
            )
        )
        or 0
    ) + 1
    stage_run = StageRun(
        prospect_id=prospect.id,
        stage=stage.value,
        attempt=attempt,
        prompt_version=version_hash,
        input_artifact_ids=[
            artifact.id
            for input_stage in STAGE_ORDER[: stage_index(stage)]
            if (artifact := latest_artifact(session, prospect.id, input_stage)) is not None
        ],
    )
    session.add(stage_run)
    prospect.status = "running"
    append_audit(
        session,
        "stage.started",
        stage.value,
        {"stage_run_id": stage_run.id, "attempt": attempt, "prompt_version": version_hash},
        prospect.id,
    )
    session.commit()

    research_run = session.get(ResearchRun, prospect.research_run_id)
    if research_run is None:
        raise WorkflowError("Research run not found")
    runtime = AgentRuntime(settings)
    try:
        context = prospect_context(session, prospect)
        context["artifact_versions"] = {
            input_stage.value: input_artifact.version
            for input_stage in STAGE_ORDER[: stage_index(stage)]
            if (input_artifact := latest_artifact(session, prospect.id, input_stage)) is not None
        }
        output = await runtime.run(
            stage,
            context,
            inputs,
            attempt,
            latest_revision_feedback(session, prospect.id, stage),
        )
        current_version = (
            session.scalar(
                select(func.max(Artifact.version)).where(
                    Artifact.prospect_id == prospect.id, Artifact.stage == stage.value
                )
            )
            or 0
        )
        payload = output.model_dump(mode="json")
        artifact = Artifact(
            prospect_id=prospect.id,
            stage=stage.value,
            version=current_version + 1,
            payload=payload,
            content_hash=content_hash(payload),
        )
        session.add(artifact)
        session.flush()

        if stage == Stage.MAKER:
            token = secrets.token_urlsafe(24)
            token_hash = hashlib.sha256(token.encode()).hexdigest()
            session.execute(
                update(PreviewToken)
                .where(PreviewToken.prospect_id == prospect.id, PreviewToken.revoked_at.is_(None))
                .values(revoked_at=utcnow())
            )
            preview_path = f"/preview/{token}"
            payload["artifact"]["preview_path"] = preview_path
            artifact.payload = deepcopy(payload)
            artifact.content_hash = content_hash(payload)
            prospect.preview_path = preview_path
            session.add(
                PreviewToken(
                    prospect_id=prospect.id,
                    token_hash=token_hash,
                    artifact_id=artifact.id,
                    expires_at=utcnow() + timedelta(days=30),
                )
            )

        stage_run.output_artifact_id = artifact.id
        stage_run.usage = payload.get("usage", {})
        stage_run.status = "completed"
        stage_run.completed_at = utcnow()
        job.status = "completed"
        append_audit(
            session,
            "artifact.created",
            stage.value,
            {
                "artifact_id": artifact.id,
                "version": artifact.version,
                "hash": artifact.content_hash,
                "handoff": payload["handoff"],
            },
            prospect.id,
        )
        _apply_handoff(session, prospect, stage, artifact, output.handoff, pipeline)
        session.commit()
        return {
            "job_id": job.id,
            "prospect_id": prospect.id,
            "stage": stage.value,
            "status": prospect.status,
            "handoff": output.handoff.model_dump(mode="json"),
        }
    except Exception as exc:
        session.rollback()
        retry_job = session.get(Job, job.id)
        retry_prospect = session.get(Prospect, prospect.id)
        failed_stage_run = session.get(StageRun, stage_run.id)
        if failed_stage_run:
            failed_stage_run.status = "failed"
            failed_stage_run.error = str(exc)
            failed_stage_run.completed_at = utcnow()
        if retry_job:
            retry_job.last_error = str(exc)
            if retry_job.attempt < 3:
                retry_job.status = "queued"
                retry_job.available_at = utcnow() + timedelta(seconds=2**retry_job.attempt)
            else:
                retry_job.status = "failed"
                if retry_prospect:
                    retry_prospect.status = "failed"
        append_audit(
            session,
            "stage.failed",
            stage.value,
            {"error": str(exc), "attempt": retry_job.attempt if retry_job else attempt},
            retry_prospect.id if retry_prospect else prospect.id,
        )
        session.commit()
        return {
            "job_id": retry_job.id if retry_job else None,
            "status": "retrying"
            if retry_job and retry_job.status == "queued"
            else "failed",
            "error": str(exc),
        }


def _apply_handoff(  # type: ignore[no-untyped-def]
    session: Session,
    prospect: Prospect,
    stage: Stage,
    artifact: Artifact,
    decision,
    pipeline: PipelineRun | None,
) -> None:
    destination = decision.destination
    recorded_decision = decision.action.value
    recorded_reason = decision.reason
    if decision.action == DecisionKind.ADVANCE:
        destination = next_stage(stage)
        if stage == Stage.RESEARCHER:
            research_run = session.get(ResearchRun, prospect.research_run_id)
            threshold = research_run.opportunity_threshold if research_run else 90
            score = int(artifact.payload["artifact"]["opportunity_score"])
            append_audit(
                session,
                "opportunity.threshold_evaluated",
                "workflow",
                {"score": score, "threshold": threshold, "passed": score > threshold},
                prospect.id,
            )
            if score <= threshold:
                destination = None
                recorded_decision = "filtered"
                recorded_reason = (
                    f"Opportunity score {score} did not exceed the configured threshold "
                    f"of {threshold}."
                )
                prospect.status = "not_qualified"
                if pipeline:
                    pipeline.status = "filtered"
                    pipeline.completed_at = utcnow()
        if destination:
            prospect.current_stage = destination.value
            prospect.status = "queued"
            enqueue_job(session, prospect, destination)
        elif recorded_decision != "filtered":
            prospect.current_stage = stage.value
            prospect.status = "curated"
            if pipeline:
                pipeline.status = "completed"
                pipeline.completed_at = utcnow()
            manager_payload = artifact.payload["artifact"]
            prospect.priority = manager_payload.get("priority", "medium")
    elif decision.action in {DecisionKind.REVISE_PREVIOUS, DecisionKind.REVISE_STAGE}:
        if decision.action == DecisionKind.REVISE_PREVIOUS:
            index = max(0, stage_index(stage) - 1)
            destination = STAGE_ORDER[index]
        if destination is None or stage_index(destination) >= stage_index(stage):
            raise WorkflowError("Revision must target an earlier stage")
        if prospect.revision_count >= 2:
            prospect.status = "needs_human_review"
            destination = None
        else:
            prospect.revision_count += 1
            session.execute(
                update(Artifact)
                .where(
                    Artifact.prospect_id == prospect.id,
                    Artifact.stage.in_(
                        [s.value for s in STAGE_ORDER[stage_index(destination) :]]
                    ),
                    Artifact.stale.is_(False),
                )
                .values(stale=True)
            )
            prospect.current_stage = destination.value
            prospect.status = "queued"
            enqueue_job(session, prospect, destination)
    elif decision.action == DecisionKind.NEEDS_HUMAN_REVIEW:
        prospect.status = "needs_human_review"
    else:
        prospect.status = "rejected"
        if pipeline:
            pipeline.status = "rejected"
            pipeline.completed_at = utcnow()

    session.add(
        Handoff(
            prospect_id=prospect.id,
            from_stage=stage.value,
            to_stage=destination.value if destination else None,
            decision=recorded_decision,
            reason=recorded_reason,
        )
    )
    append_audit(
        session,
        "handoff.recorded",
        stage.value,
        {
            "from": stage.value,
            "to": destination.value if destination else None,
            "decision": recorded_decision,
            "reason": recorded_reason,
        },
        prospect.id,
    )


async def worker_tick(session: Session, settings: Settings, limit: int = 3) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for _ in range(max(1, min(limit, 10))):
        job = claim_job(session)
        if not job:
            break
        results.append(await process_job(session, settings, job))
    return results


def next_queued_job(session: Session, prospect_id: str) -> Job | None:
    return session.scalar(
        select(Job)
        .where(Job.prospect_id == prospect_id, Job.status == "queued")
        .order_by(Job.created_at)
        .limit(1)
    )


async def process_job_by_id(
    session: Session, settings: Settings, job_id: str
) -> dict[str, Any]:
    existing = session.get(Job, job_id)
    if not existing:
        raise WorkflowError("Job not found")
    prospect_id = existing.prospect_id
    if existing.status in {"completed", "cancelled", "failed"}:
        successor = next_queued_job(session, prospect_id)
        return {
            "job_id": existing.id,
            "status": existing.status,
            "next_job_id": successor.id if successor else None,
        }
    if existing.status == "running":
        return {"job_id": existing.id, "status": "running", "next_job_id": None}
    job = claim_job(session, job_id)
    if not job:
        return {"job_id": job_id, "status": "not_ready", "next_job_id": None}
    result = await process_job(session, settings, job)
    successor = next_queued_job(session, prospect_id)
    result["next_job_id"] = successor.id if successor else None
    return result


async def recover_queued_jobs(
    session: Session, settings: Settings, limit: int = 10
) -> list[str]:
    stale_before = utcnow() - timedelta(minutes=2)
    jobs = session.scalars(
        select(Job)
        .where(
            Job.available_at <= utcnow(),
            or_(
                Job.status == "publish_failed",
                (Job.status == "queued") & (Job.created_at <= stale_before),
            ),
        )
        .order_by(Job.created_at)
        .limit(max(1, min(limit, 10)))
    ).all()
    job_ids = [job.id for job in jobs]
    if not job_ids:
        return []
    try:
        await publish_jobs(settings, job_ids)
    except QueuePublishError as exc:
        session.execute(
            update(Job)
            .where(Job.id.in_(job_ids), Job.status.in_(("queued", "publish_failed")))
            .values(status="publish_failed", last_error=str(exc))
        )
        append_audit(
            session,
            "queue.recovery_failed",
            "admin",
            {"job_ids": job_ids, "error": str(exc)},
        )
        session.commit()
        raise
    session.execute(
        update(Job)
        .where(Job.id.in_(job_ids), Job.status == "publish_failed")
        .values(status="queued", last_error=None)
    )
    append_audit(
        session,
        "queue.recovered",
        "admin",
        {"job_ids": job_ids, "count": len(job_ids)},
    )
    session.commit()
    return job_ids
