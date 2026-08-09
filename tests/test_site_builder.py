import pytest

from fivefold.site_builder import sanitize_site, validate_site


def test_live_generated_site_validation() -> None:
    html = (
        '<html><head><meta name="viewport" content="width=device-width"></head>'
        "<body><main><h1>Business</h1></main></body></html>"
    )
    css = "@media(max-width: 800px){main{display:block}}"
    assert validate_site(html, css).passed


def test_sanitizer_rejects_executable_content() -> None:
    with pytest.raises(ValueError):
        sanitize_site("<main><script>alert(1)</script></main>", "")
    with pytest.raises(ValueError):
        sanitize_site("<main></main>", "@import url('https://example.com/x.css')")
