from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpx

from fivefold.config import Settings
from fivefold.contracts import WebsiteAudit

SOCIAL_HOSTS = {
    "facebook.com",
    "www.facebook.com",
    "instagram.com",
    "www.instagram.com",
    "linkedin.com",
    "www.linkedin.com",
    "tiktok.com",
    "www.tiktok.com",
}


class ResearchProviderError(RuntimeError):
    pass


async def live_candidates(
    settings: Settings,
    location: str,
    categories: list[str],
    max_businesses: int,
) -> list[dict[str, Any]]:
    if not settings.google_maps_api_key:
        raise ResearchProviderError("GOOGLE_MAPS_API_KEY is required for live research")

    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    field_mask = ",".join(
        [
            "places.id",
            "places.displayName",
            "places.primaryTypeDisplayName",
            "places.formattedAddress",
            "places.websiteUri",
            "places.googleMapsUri",
        ]
    )
    async with httpx.AsyncClient(timeout=20) as client:
        for category in categories:
            response = await client.post(
                "https://places.googleapis.com/v1/places:searchText",
                headers={
                    "X-Goog-Api-Key": settings.google_maps_api_key,
                    "X-Goog-FieldMask": field_mask,
                },
                json={
                    "textQuery": f"{category} in {location}",
                    "pageSize": min(20, max_businesses * 2),
                    "regionCode": "IE",
                    "languageCode": "en",
                },
            )
            response.raise_for_status()
            for place in response.json().get("places", []):
                place_id = place.get("id")
                if not place_id or place_id in seen:
                    continue
                seen.add(place_id)
                website_url = place.get("websiteUri")
                footprint = classify_footprint(website_url)
                audit = await audit_website(website_url)
                if footprint == "adequate" and audit.score < 70:
                    footprint = "weak"
                if footprint == "adequate":
                    continue
                candidates.append(
                    {
                        "business_name": place.get("displayName", {}).get("text", "Unverified business"),
                        "category": place.get("primaryTypeDisplayName", {}).get("text", category),
                        "location": place.get("formattedAddress", location),
                        "place_id": place_id,
                        "website_url": website_url,
                        "footprint": footprint,
                        "qualification_reason": qualification_reason(footprint, audit),
                        # Raw reviews are deliberately not retained. Live review themes can be added
                        # only by a compliant, attributed enrichment step.
                        "review_themes": [],
                        "audit": audit.model_dump(mode="json"),
                        "opportunity_score": opportunity_score(footprint, audit),
                        "google_maps_uri": place.get("googleMapsUri"),
                    }
                )
                if len(candidates) >= max_businesses:
                    return candidates
    return candidates


def classify_footprint(website_url: str | None) -> str:
    if not website_url:
        return "absent"
    hostname = (urlparse(website_url).hostname or "").lower()
    if hostname in SOCIAL_HOSTS:
        return "social_only"
    return "adequate"


async def audit_website(url: str | None) -> WebsiteAudit:
    if not url:
        return WebsiteAudit(findings=["No owned website URL was found."], score=5)
    if classify_footprint(url) == "social_only":
        return WebsiteAudit(
            reachable=True,
            https=url.startswith("https://"),
            mobile_meta=True,
            findings=["Social profile only", "No owned conversion page"],
            score=32,
        )

    findings: list[str] = []
    try:
        async with httpx.AsyncClient(
            timeout=10,
            follow_redirects=True,
            headers={"User-Agent": "FivefoldWebAudit/1.0 (+concept research)"},
        ) as client:
            response = await client.get(url)
        html = response.text[:500_000].lower()
        reachable = response.status_code < 400
    except (httpx.HTTPError, ValueError):
        return WebsiteAudit(findings=["Website could not be reached during the audit."], score=10)

    https = str(response.url).startswith("https://")
    mobile = "name=\"viewport\"" in html or "name='viewport'" in html
    cta = bool(re.search(r"contact|book|quote|call|enquire|appointment", html))
    contact = bool(re.search(r"mailto:|tel:|contact", html))
    lead_form = "<form" in html
    checks = [(https, "HTTPS is not active"), (mobile, "Mobile viewport metadata is missing"), (cta, "No clear call to action was detected"), (contact, "Contact route is not visible"), (lead_form, "No enquiry form was detected")]
    findings.extend(message for passed, message in checks if not passed)
    score = int(sum(20 for passed, _ in checks if passed))
    return WebsiteAudit(
        reachable=reachable,
        https=https,
        mobile_meta=mobile,
        clear_cta=cta,
        contact_visible=contact,
        lead_form=lead_form,
        findings=findings,
        score=score,
    )


def qualification_reason(footprint: str, audit: WebsiteAudit) -> str:
    if footprint == "absent":
        return "No owned website was found; customers lack a controlled enquiry destination."
    if footprint == "social_only":
        return "The business relies on a social profile rather than an owned conversion page."
    return f"The existing website scored {audit.score}/100 on the bounded lead-readiness audit."


def opportunity_score(footprint: str, audit: WebsiteAudit) -> int:
    base = {"absent": 95, "social_only": 88, "weak": 78, "adequate": 30}[footprint]
    return max(0, min(100, int(base - audit.score * 0.1)))


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:50] or "local-business"


async def check_ie_domain(domain: str) -> tuple[bool | None, datetime]:
    """Use .IE RDAP where available. Unknown is safer than claiming availability."""
    checked_at = datetime.now(UTC)
    url = f"https://rdap.weare.ie/domain/{domain}"
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            response = await client.get(url)
        if response.status_code == 404:
            return True, checked_at
        if response.status_code == 200:
            return False, checked_at
    except httpx.HTTPError:
        pass
    return None, checked_at
