from __future__ import annotations

import httpx

from fivefold.config import Settings


class QueuePublishError(RuntimeError):
    pass


async def publish_jobs(settings: Settings, job_ids: list[str]) -> None:
    if not job_ids:
        return
    if not settings.is_production:
        return
    url = f"{settings.base_url.rstrip('/')}/api/queue_publish"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                url,
                headers={"Authorization": f"Bearer {settings.cron_secret}"},
                json={"jobIds": job_ids},
            )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise QueuePublishError(f"Could not publish stage-ready event: {exc}") from exc
