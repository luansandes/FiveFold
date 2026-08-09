from sqlalchemy import func, select
from sqlalchemy.orm import Session

from fivefold.db import get_engine, init_db
from fivefold.models import OperationalSetting, PricingSetting, Prospect, ResearchRun
from fivefold.reset import reset_generated_data


def add_prospect(db: Session) -> Prospect:
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
        business_name="Reset Business",
        category="Painter",
        place_id="reset-place-id",
        footprint="absent",
        qualification_reason="No website",
    )
    db.add(prospect)
    db.commit()
    return prospect


def test_generated_data_reset_preserves_pricing_and_reseeds_operations(db: Session) -> None:
    add_prospect(db)
    pricing_before = db.scalar(select(func.count()).select_from(PricingSetting))
    result = reset_generated_data(get_engine())
    init_db()
    db.expire_all()
    assert result["deleted"]["prospects"] == 1
    assert db.scalar(select(func.count()).select_from(Prospect)) == 0
    assert db.scalar(select(func.count()).select_from(PricingSetting)) == pricing_before
    operational = db.scalar(select(OperationalSetting))
    assert operational is not None
    assert operational.max_prospects_per_run == 1
    assert operational.opportunity_score_threshold == 90
