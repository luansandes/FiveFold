from __future__ import annotations

import asyncio

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
    last_error: httpx.HTTPError | None = None
    async with httpx.AsyncClient(timeout=15) as client:
        for attempt in range(1, 4):
            try:
                response = await client.post(
                    url,
                    headers={"Authorization": f"Bearer {settings.cron_secret}"},
                    json={"jobIds": job_ids},
                )
                response.raise_for_status()
                return
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt < 3:
                    await asyncio.sleep(0.25 * (2 ** (attempt - 1)))
    if last_error is not None:
        raise QueuePublishError(
            f"Could not publish stage-ready event after 3 attempts: {last_error}"
        ) from last_error
