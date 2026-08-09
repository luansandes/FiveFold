from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest

from fivefold.config import Settings
from fivefold.queueing import QueuePublishError, publish_jobs


def production_settings() -> Settings:
    return Settings(
        app_env="production",
        base_url="https://fivefold.test",
        cron_secret="internal-secret",
    )


def async_client(post_results: list[object]) -> AsyncMock:
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.post.side_effect = post_results
    return client


def test_publisher_retries_transient_failures_then_succeeds() -> None:
    request = httpx.Request("POST", "https://fivefold.test/api/queue_publish")
    response = Mock()
    response.raise_for_status.return_value = None
    client = async_client(
        [
            httpx.ConnectError("temporary one", request=request),
            httpx.ConnectError("temporary two", request=request),
            response,
        ]
    )

    with (
        patch("fivefold.queueing.httpx.AsyncClient", return_value=client),
        patch("fivefold.queueing.asyncio.sleep", new=AsyncMock()) as sleep,
    ):
        asyncio.run(publish_jobs(production_settings(), ["job-one"]))

    assert client.post.await_count == 3
    assert sleep.await_count == 2


def test_publisher_surfaces_failure_after_three_attempts() -> None:
    request = httpx.Request("POST", "https://fivefold.test/api/queue_publish")
    client = async_client(
        [
            httpx.ConnectError(f"temporary {attempt}", request=request)
            for attempt in range(3)
        ]
    )

    with (
        patch("fivefold.queueing.httpx.AsyncClient", return_value=client),
        patch("fivefold.queueing.asyncio.sleep", new=AsyncMock()),
        pytest.raises(QueuePublishError, match="after 3 attempts"),
    ):
        asyncio.run(publish_jobs(production_settings(), ["job-one"]))

    assert client.post.await_count == 3
