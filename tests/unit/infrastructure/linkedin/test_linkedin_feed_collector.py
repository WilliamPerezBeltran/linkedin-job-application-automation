"""Unit tests for `app.infrastructure.linkedin.linkedin_feed_collector`.

Cubre `build_raw_feed_posts`, la función pura (sin Playwright) que traduce
HTML crudo ya extraído a `RawFeedPost`, calculando `content_hash`. No
instancia `LinkedInFeedCollector` en sí (eso requeriría un navegador real,
prohibido en tests, ver `05_linkedin.md`).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from app.infrastructure.linkedin.linkedin_feed_collector import build_raw_feed_posts

pytestmark = pytest.mark.unit

_FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> str:
    return (_FIXTURES_DIR / name).read_text(encoding="utf-8")


def test_build_raw_feed_posts_maps_fields_and_computes_content_hash() -> None:
    html = _load_fixture("well_formed_post.html")

    posts = build_raw_feed_posts([html])

    assert len(posts) == 1
    post = posts[0]
    assert post.author == "Jane Recruiter"
    assert "Senior Backend Engineer" in post.content
    assert post.content_hash == hashlib.sha256(post.content.encode("utf-8")).hexdigest()
    assert post.url.startswith("https://www.linkedin.com/feed/update/")


def test_build_raw_feed_posts_skips_posts_without_extractable_content() -> None:
    good_html = _load_fixture("well_formed_post.html")
    empty_html = _load_fixture("missing_content.html")

    posts = build_raw_feed_posts([empty_html, good_html])

    # El post sin contenido se descarta; el resto de la corrida no se aborta.
    assert len(posts) == 1
    assert "Senior Backend Engineer" in posts[0].content


def test_build_raw_feed_posts_is_empty_for_no_input() -> None:
    assert build_raw_feed_posts([]) == []
