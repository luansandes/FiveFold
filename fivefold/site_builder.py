from __future__ import annotations

import hashlib
import html
import re
from typing import Any

from fivefold.contracts import DesignSpecification, ValidationReport, WebsiteArtifact

FORBIDDEN_PATTERNS = [
    r"<script\b",
    r"<iframe\b",
    r"<form\b",
    r"javascript:",
    r"\s+on[a-z]+\s*=",
]


def validate_site(html_text: str, css_text: str) -> ValidationReport:
    lowered = html_text.lower()
    checks = {
        "no_scripts": "<script" not in lowered,
        "no_iframes": "<iframe" not in lowered,
        "no_active_forms": "<form" not in lowered,
        "has_main": "<main" in lowered,
        "has_heading": "<h1" in lowered,
        "has_viewport": "viewport" in lowered,
        "responsive_css": "@media" in css_text,
        "no_javascript_urls": "javascript:" not in lowered,
    }
    warnings = [name.replace("_", " ") for name, passed in checks.items() if not passed]
    return ValidationReport(passed=all(checks.values()), checks=checks, warnings=warnings)


def sanitize_site(html_text: str, css_text: str) -> tuple[str, str]:
    for pattern in FORBIDDEN_PATTERNS:
        if re.search(pattern, html_text, flags=re.IGNORECASE):
            raise ValueError(f"Generated HTML contains forbidden pattern: {pattern}")
    if re.search(r"@import|url\s*\(\s*['\"]?https?", css_text, flags=re.IGNORECASE):
        raise ValueError("Generated CSS contains external resources")
    return html_text, css_text


def build_fixture_site(
    business_name: str,
    category: str,
    location: str,
    design: DesignSpecification,
    design_version: int,
) -> WebsiteArtifact:
    escaped_name = html.escape(business_name)
    escaped_category = html.escape(category)
    escaped_location = html.escape(location)
    primary = design.palette.get("brand", "#e35f37")
    ink = design.palette.get("ink", "#17231e")
    paper = design.palette.get("paper", "#fffaf4")
    accent = design.palette.get("accent", "#f4c45e")

    section_html = []
    for section in design.sections:
        points = "".join(f"<li>{html.escape(point)}</li>" for point in section.content_points)
        section_html.append(
            f'<section class="section" id="{html.escape(section.section_type)}">'
            f'<p class="eyebrow">{html.escape(section.section_type)}</p>'
            f'<h2>{html.escape(section.heading)}</h2>'
            f'<p>{html.escape(section.purpose)}</p><ul>{points}</ul></section>'
        )

    html_text = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escaped_name} — concept website</title>
  <meta name="description" content="A concept landing page for {escaped_name}, {escaped_category} in {escaped_location}.">
</head>
<body itemscope itemtype="https://schema.org/LocalBusiness">
  <header class="nav"><a class="brand" itemprop="name" href="#top">{escaped_name}</a><span itemprop="areaServed">{escaped_location}</span></header>
  <main id="top">
    <section class="hero">
      <p class="eyebrow">Local {escaped_category}</p>
      <h1>Thoughtful local service, made simple.</h1>
      <p class="lede">A clearer way for customers in {escaped_location} to understand the service and take the next step.</p>
      <a class="button" href="#contact">{html.escape(design.primary_cta)}</a>
    </section>
    <div class="sections">{''.join(section_html)}</div>
    <section class="testimonial"><p class="eyebrow">Customer stories</p><h2>Testimonials belong here</h2><p>Placeholder — publish only after the business supplies or approves a customer testimonial.</p></section>
    <section class="contact" id="contact"><h2>Ready to start a conversation?</h2><p>This concept does not collect personal information. Contact details are added only after business approval.</p><span class="button muted">Enquiry disabled in preview</span></section>
  </main>
  <footer>{escaped_name} · Concept content pending business approval</footer>
</body>
</html>"""
    css_text = f"""
:root{{--ink:{ink};--brand:{primary};--accent:{accent};--paper:{paper};--white:#fff;}}
*{{box-sizing:border-box}}html{{scroll-behavior:smooth}}body{{margin:0;background:var(--paper);color:var(--ink);font-family:Inter,ui-sans-serif,system-ui,sans-serif;line-height:1.6}}
.nav{{display:flex;justify-content:space-between;align-items:center;padding:1.25rem clamp(1.25rem,5vw,5rem);font-size:.9rem}}.brand{{font-weight:800;color:inherit;text-decoration:none}}
.hero{{min-height:70vh;display:grid;align-content:center;padding:5rem clamp(1.25rem,8vw,9rem);background:linear-gradient(135deg,var(--paper),color-mix(in srgb,var(--accent) 24%,white))}}
.eyebrow{{text-transform:uppercase;letter-spacing:.16em;font-size:.73rem;font-weight:800;color:var(--brand)}}h1{{font-size:clamp(3rem,8vw,7rem);line-height:.94;max-width:12ch;margin:.3rem 0 1.5rem}}h2{{font-size:clamp(1.8rem,4vw,3.5rem);line-height:1.05;margin:.2rem 0 1rem}}.lede{{font-size:1.2rem;max-width:55ch}}
.button{{display:inline-block;width:max-content;margin-top:1rem;padding:.9rem 1.2rem;border-radius:999px;background:var(--brand);color:white;text-decoration:none;font-weight:800}}.button:focus-visible{{outline:4px solid var(--accent);outline-offset:4px}}.muted{{opacity:.75}}
.sections{{display:grid;grid-template-columns:repeat(3,1fr);gap:1px;background:color-mix(in srgb,var(--ink) 15%,transparent)}}.section{{padding:clamp(2rem,5vw,5rem);background:var(--white)}}ul{{padding-left:1.2rem}}
.testimonial,.contact{{padding:clamp(3rem,8vw,8rem);max-width:1100px;margin:auto}}.testimonial{{border-bottom:1px solid color-mix(in srgb,var(--ink) 14%,transparent)}}footer{{padding:2rem clamp(1.25rem,5vw,5rem);font-size:.85rem}}
@media(max-width:800px){{.sections{{grid-template-columns:1fr}}.nav span{{display:none}}.hero{{min-height:62vh}}}}
""".strip()
    sanitize_site(html_text, css_text)
    validation = validate_site(html_text, css_text)
    digest = hashlib.sha256((html_text + css_text).encode()).hexdigest()
    structured_data: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": "LocalBusiness",
        "name": business_name,
        "address": {"@type": "PostalAddress", "addressLocality": location},
        "description": f"Concept profile for a local {category}.",
    }
    return WebsiteArtifact(
        title=f"{business_name} — concept website",
        html=html_text,
        css=css_text,
        meta_description=f"Concept landing page for {business_name}, {category} in {location}.",
        structured_data=structured_data,
        content_manifest=[section.heading for section in design.sections],
        validation=validation,
        artefact_hash=digest,
        inherited_design_version=design_version,
    )


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
