from __future__ import annotations

import html
import re

from fivefold.contracts import ValidationChecks, ValidationReport, WebsiteArtifact

FORBIDDEN_PATTERNS = [
    r"<script\b",
    r"<iframe\b",
    r"<form\b",
    r"javascript:",
    r"\s+on[a-z]+\s*=",
]


def validate_site(html_text: str, css_text: str) -> ValidationReport:
    lowered = html_text.lower()
    checks = ValidationChecks(
        no_scripts="<script" not in lowered,
        no_iframes="<iframe" not in lowered,
        no_active_forms="<form" not in lowered,
        has_main="<main" in lowered,
        has_heading="<h1" in lowered,
        has_viewport="viewport" in lowered,
        responsive_css="@media" in css_text,
        no_javascript_urls="javascript:" not in lowered,
    )
    values = checks.model_dump()
    warnings = [name.replace("_", " ") for name, passed in values.items() if not passed]
    return ValidationReport(passed=all(values.values()), checks=checks, warnings=warnings)


def sanitize_site(html_text: str, css_text: str) -> tuple[str, str]:
    for pattern in FORBIDDEN_PATTERNS:
        if re.search(pattern, html_text, flags=re.IGNORECASE):
            raise ValueError(f"Generated HTML contains forbidden pattern: {pattern}")
    if re.search(r"@import|url\s*\(\s*['\"]?https?", css_text, flags=re.IGNORECASE):
        raise ValueError("Generated CSS contains external resources")
    return html_text, css_text


def render_public_preview(artifact: WebsiteArtifact, disclosure: str) -> str:
    style = f"<style>{artifact.css}</style>"
    banner = (
        '<aside class="fivefold-disclosure" role="note">'
        f"{html.escape(disclosure)}</aside>"
        "<style>.fivefold-disclosure{position:relative;z-index:9999;padding:.75rem 1rem;"
        "background:#111827;color:#fff;font:600 13px/1.45 system-ui;text-align:center}</style>"
    )
    document = artifact.html.replace("</head>", f"{style}</head>")
    return re.sub(r"<body([^>]*)>", rf"<body\1>{banner}", document, count=1)
