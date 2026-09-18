"""Unit tests for `app.infrastructure.linkedin.parser`.

Usa fixtures HTML estáticas (`tests/unit/infrastructure/linkedin/fixtures/`)
— nunca scraping real contra LinkedIn, ver `05_linkedin.md`.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from app.infrastructure.linkedin.exceptions import EmptyPostContentError
from app.infrastructure.linkedin.parser import parse_post

pytestmark = pytest.mark.unit

_FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> str:
    return (_FIXTURES_DIR / name).read_text(encoding="utf-8")


def test_parse_post_well_formed_extracts_all_fields() -> None:
    html = _load_fixture("well_formed_post.html")
    fixed_now = datetime(2026, 1, 1, 12, 0, 0)

    parsed = parse_post(html, now=fixed_now)

    assert parsed.author == "Jane Recruiter"
    assert "Senior Backend Engineer" in parsed.content
    assert "Apply now!" in parsed.content
    # Sin saltos de línea/espacios duplicados sobrevivientes del <br>.
    assert "\n" not in parsed.content
    assert "  " not in parsed.content
    assert parsed.url == (
        "https://www.linkedin.com/feed/update/urn:li:activity:7000000000000000001/"
    )
    # "2h" relativo a `now` -> exactamente 2 horas antes.
    assert parsed.published_at == datetime(2026, 1, 1, 10, 0, 0)


def test_parse_post_tolerates_missing_optional_fields() -> None:
    html = _load_fixture("missing_optional_fields.html")

    parsed = parse_post(html)

    # No revienta por falta de autor/timestamp/URL — degrada a defaults.
    assert parsed.author  # fallback no vacío
    assert parsed.published_at is None
    assert parsed.url == ""
    assert "Machine Learning Engineer" in parsed.content


def test_parse_post_raises_when_content_is_missing() -> None:
    html = _load_fixture("missing_content.html")

    with pytest.raises(EmptyPostContentError):
        parse_post(html)
