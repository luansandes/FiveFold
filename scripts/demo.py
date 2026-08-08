from __future__ import annotations

import asyncio

from fivefold.config import get_settings
from fivefold.contracts import ResearchRunRequest
from fivefold.db import get_session_factory, init_db
from fivefold.workflow import create_research_run, run_fixture_to_completion


async def main() -> None:
    init_db()
    session = get_session_factory()()
    try:
        run = await create_research_run(
            session,
            get_settings(),
            ResearchRunRequest(provider="fixture", max_businesses=3),
        )
        results = await run_fixture_to_completion(session, get_settings())
        print(f"Research run {run.id}: {len(results)} stage executions completed")
    finally:
        session.close()


if __name__ == "__main__":
    asyncio.run(main())

