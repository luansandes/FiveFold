import pytest

from fivefold.contracts import DesignSpecification, PageSection
from fivefold.site_builder import build_fixture_site, sanitize_site, validate_site


def design() -> DesignSpecification:
    return DesignSpecification(
        concept_name="Test concept",
        audience="Local customers",
        primary_goal="Start a conversation",
        user_journey=["Learn", "Trust", "Act"],
        sections=[
            PageSection(
                section_type="services",
                heading="Services",
                purpose="Explain the offer",
                content_points=["One", "Two"],
            )
        ],
        palette={"ink": "#111111", "brand": "#a33a20", "accent": "#f2c85d", "paper": "#fffaf2"},
        typography={"display": "system", "body": "system"},
        primary_cta="Ask about availability",
        trust_strategy=["Verified facts only"],
        accessibility_requirements=["AA contrast"],
        mobile_behaviour=["Single column"],
    )


def test_generated_site_is_tangible_and_inert() -> None:
    artifact = build_fixture_site(
        "Test Business", "Painter", "Dublin", design(), design_version=1
    )
    assert artifact.validation.passed
    assert "Test Business" in artifact.html
    assert "<form" not in artifact.html.lower()
    assert "<script" not in artifact.html.lower()
    assert "itemscope" in artifact.html
    assert validate_site(artifact.html, artifact.css).passed


def test_sanitizer_rejects_executable_content() -> None:
    with pytest.raises(ValueError):
        sanitize_site("<main><script>alert(1)</script></main>", "")
    with pytest.raises(ValueError):
        sanitize_site("<main></main>", "@import url('https://example.com/x.css')")

