from __future__ import annotations

from fivefold.contracts import ProfitabilityEstimate, ServiceOffer

DEFAULT_PRICING = {
    "monthly_eur": 14.99,
    "annual_eur": 149.99,
    "three_year_eur": 439.99,
    "domain_annual_eur": 23.99,
    "platform_allocation_annual_eur": 24.0,
    "data_api_allocation_annual_eur": 12.0,
    "operations_reserve_annual_eur": 18.0,
    "early_cancellation_risk_eur": 38.0,
    "vat_rate": 0.23,
    "source_note": "Editable ex-VAT assumptions; refresh registrar and platform prices before quoting.",
}


def service_offer(values: dict[str, float | str]) -> ServiceOffer:
    return ServiceOffer(
        monthly_eur=float(values["monthly_eur"]),
        annual_eur=float(values["annual_eur"]),
        three_year_eur=float(values["three_year_eur"]),
        includes=[
            "One responsive landing page",
            "One .ie domain",
            "Shared managed hosting",
            "SSL and uptime monitoring",
            "Quarterly improvement recommendations",
        ],
        excludes=[
            "Ecommerce",
            "Bespoke integrations",
            "Active lead forms in the concept",
            "Regular content-entry labour",
            "Major redesigns",
        ],
    )


def estimate_profitability(values: dict[str, float | str]) -> ProfitabilityEstimate:
    annual_revenue = float(values["annual_eur"])
    annual_cost = round(
        sum(
            float(values[key])
            for key in (
                "domain_annual_eur",
                "platform_allocation_annual_eur",
                "data_api_allocation_annual_eur",
                "operations_reserve_annual_eur",
            )
        ),
        2,
    )
    contribution = round(annual_revenue - annual_cost, 2)
    margin = round((contribution / annual_revenue * 100) if annual_revenue else 0, 1)
    return ProfitabilityEstimate(
        annual_revenue_eur=annual_revenue,
        estimated_annual_cost_eur=annual_cost,
        contribution_eur=contribution,
        gross_margin_percent=margin,
        early_cancellation_risk_eur=float(values["early_cancellation_risk_eur"]),
        assumptions=[
            f"€{float(values['domain_annual_eur']):.2f} ex-VAT domain reference",
            "Shared Vercel and database allocation",
            "Automated maintenance; human content labour excluded",
        ],
    )

