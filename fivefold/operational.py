from __future__ import annotations

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from fivefold.models import OperationalSetting, utcnow

DEFAULT_MAX_PROSPECTS_PER_RUN = 1
DEFAULT_OPPORTUNITY_SCORE_THRESHOLD = 90


def current_operational_setting(session: Session) -> OperationalSetting:
    current = session.scalar(
        select(OperationalSetting).order_by(desc(OperationalSetting.effective_at)).limit(1)
    )
    if current:
        return current
    current = OperationalSetting(
        name=f"operations-{utcnow().isoformat()}",
        max_prospects_per_run=DEFAULT_MAX_PROSPECTS_PER_RUN,
        opportunity_score_threshold=DEFAULT_OPPORTUNITY_SCORE_THRESHOLD,
    )
    session.add(current)
    session.flush()
    return current
