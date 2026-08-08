from fivefold.pricing import DEFAULT_PRICING, estimate_profitability, service_offer


def test_selected_offer_and_margin_are_explicit() -> None:
    offer = service_offer(DEFAULT_PRICING)
    estimate = estimate_profitability(DEFAULT_PRICING)
    assert offer.monthly_eur == 14.99
    assert offer.annual_eur == 149.99
    assert offer.three_year_eur == 439.99
    assert offer.monthly_commitment == "Cancel anytime"
    assert estimate.contribution_eur > 0
    assert estimate.early_cancellation_risk_eur > offer.monthly_eur
    assert "human content labour excluded" in estimate.assumptions[-1]

