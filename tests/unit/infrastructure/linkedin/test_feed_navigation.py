"""Unit tests for `navigate_to_feed`/`collect_raw_posts_html`
(`app.infrastructure.linkedin.feed`).

`assert_allowed_feed_url` -- la función pura de whitelist -- ya está
cubierta en `test_feed_whitelist.py`. Este módulo cubre el resto de
`feed.py`: navegación + extracción/scroll del feed, contra un `_FakePage`
double que implementa solo el subconjunto de la interfaz de
`playwright.sync_api.Page` que este código realmente usa (`.goto`, `.url`,
`.query_selector_all`, `.mouse.wheel`) -- nunca un navegador real ni
`linkedin.com` real (`05_linkedin.md`: fixtures/dobles siempre, scraping
real nunca en tests).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import cast

import pytest
from playwright.sync_api import Page

from app.infrastructure.linkedin.config import LinkedInSessionConfig
from app.infrastructure.linkedin.exceptions import DisallowedNavigationError, LinkedInBlockedError
from app.infrastructure.linkedin.feed import collect_raw_posts_html, navigate_to_feed

pytestmark = pytest.mark.unit

_FEED_URL = "https://www.linkedin.com/feed/"


class _FakeElement:
    def __init__(self, html: str) -> None:
        self._html = html

    def inner_html(self) -> str:
        return self._html


class _FakeMouse:
    def __init__(self) -> None:
        self.wheel_calls: list[tuple[int, int]] = []

    def wheel(self, delta_x: int, delta_y: int) -> None:
        self.wheel_calls.append((delta_x, delta_y))


@dataclass
class _FakePage:
    """Stand-in for `playwright.sync_api.Page` -- `url` can be scripted to
    change on each `.goto()`/`.query_selector_all()` call via
    `url_sequence`, simulating LinkedIn redirecting mid-navigation/mid-scroll
    (a checkpoint, or an unexpected redirect away from the feed)."""

    url: str
    elements_by_call: list[list[_FakeElement]] = field(default_factory=list)
    url_after_query: list[str] | None = None
    goto_calls: list[str] = field(default_factory=list)
    mouse: _FakeMouse = field(default_factory=_FakeMouse)
    _query_call_count: int = 0

    def goto(self, url: str, *, wait_until: str) -> None:
        self.goto_calls.append(url)
        self.url = url

    def query_selector_all(self, selector: str) -> list[_FakeElement]:
        elements = (
            self.elements_by_call[self._query_call_count]
            if self._query_call_count < len(self.elements_by_call)
            else []
        )
        if self.url_after_query is not None and self._query_call_count < len(
            self.url_after_query
        ):
            self.url = self.url_after_query[self._query_call_count]
        self._query_call_count += 1
        return elements


def _as_page(page: _FakePage) -> Page:
    """Narrows a `_FakePage` double to `Page` for call sites -- `_FakePage`
    satisfies the small subset of the real `playwright.sync_api.Page`
    interface this module's functions actually use, but isn't a structural
    match for the full (huge) real class, so `mypy --strict` needs the cast
    made explicit here instead of a `# type: ignore` at every call site."""
    return cast(Page, page)


def _config(**overrides: object) -> LinkedInSessionConfig:
    defaults: dict[str, object] = {
        "max_scroll_iterations": 3,
        "scroll_delay_seconds": 0.0,
        "max_posts": None,
    }
    defaults.update(overrides)
    return LinkedInSessionConfig(**defaults)  # type: ignore[arg-type]


class TestNavigateToFeed:
    def test_navigates_and_returns_without_error_when_the_feed_loads(self) -> None:
        page = _FakePage(url="about:blank")

        navigate_to_feed(_as_page(page), feed_url=_FEED_URL)

        assert page.goto_calls == [_FEED_URL]
        assert page.url == _FEED_URL

    def test_rejects_a_feed_url_outside_the_whitelist_before_navigating(self) -> None:
        page = _FakePage(url="about:blank")

        with pytest.raises(DisallowedNavigationError):
            navigate_to_feed(_as_page(page), feed_url="https://www.linkedin.com/jobs/view/123/")

        assert page.goto_calls == []

    def test_raises_blocked_error_when_linkedin_shows_a_checkpoint_after_navigating(self) -> None:
        page = _FakePage(url="about:blank")

        def _goto_to_checkpoint(url: str, *, wait_until: str) -> None:
            page.goto_calls.append(url)
            page.url = "https://www.linkedin.com/checkpoint/challenge/"

        page.goto = _goto_to_checkpoint  # type: ignore[method-assign]

        with pytest.raises(LinkedInBlockedError):
            navigate_to_feed(_as_page(page), feed_url=_FEED_URL)


class TestCollectRawPostsHtml:
    def test_dedupes_posts_seen_across_scroll_iterations(self) -> None:
        post_a = _FakeElement("<div>Post A</div>")
        post_b = _FakeElement("<div>Post B</div>")
        page = _FakePage(
            url=_FEED_URL,
            elements_by_call=[[post_a], [post_a, post_b], [post_a, post_b]],
        )

        posts = collect_raw_posts_html(_as_page(page), config=_config(max_scroll_iterations=3))

        assert posts == ["<div>Post A</div>", "<div>Post B</div>"]
        assert len(page.mouse.wheel_calls) == 3

    def test_stops_early_once_max_posts_is_reached(self) -> None:
        post_a = _FakeElement("<div>Post A</div>")
        post_b = _FakeElement("<div>Post B</div>")
        post_c = _FakeElement("<div>Post C</div>")
        page = _FakePage(
            url=_FEED_URL,
            elements_by_call=[[post_a, post_b, post_c]],
        )

        posts = collect_raw_posts_html(
            _as_page(page), config=_config(max_scroll_iterations=5, max_posts=2)
        )

        assert posts == ["<div>Post A</div>", "<div>Post B</div>"]
        # Nunca llega a scrollear -- corta apenas alcanza `max_posts`
        # dentro de la primera iteración.
        assert page.mouse.wheel_calls == []

    def test_raises_blocked_error_when_a_checkpoint_appears_mid_scroll(self) -> None:
        post_a = _FakeElement("<div>Post A</div>")
        page = _FakePage(
            url=_FEED_URL,
            elements_by_call=[[post_a], []],
            url_after_query=["https://www.linkedin.com/checkpoint/challenge/"],
        )

        with pytest.raises(LinkedInBlockedError):
            collect_raw_posts_html(_as_page(page), config=_config(max_scroll_iterations=3))

    def test_raises_disallowed_navigation_error_when_redirected_away_from_feed_mid_scroll(
        self,
    ) -> None:
        post_a = _FakeElement("<div>Post A</div>")
        page = _FakePage(
            url=_FEED_URL,
            elements_by_call=[[post_a], []],
            url_after_query=["https://www.linkedin.com/in/someone/"],
        )

        with pytest.raises(DisallowedNavigationError):
            collect_raw_posts_html(_as_page(page), config=_config(max_scroll_iterations=3))

    def test_raises_immediately_if_the_page_starts_outside_the_feed(self) -> None:
        page = _FakePage(url="https://www.linkedin.com/jobs/view/123/")

        with pytest.raises(DisallowedNavigationError):
            collect_raw_posts_html(_as_page(page), config=_config())

    def test_returns_empty_list_when_no_posts_are_found(self) -> None:
        page = _FakePage(url=_FEED_URL, elements_by_call=[[], [], []])

        posts = collect_raw_posts_html(_as_page(page), config=_config(max_scroll_iterations=3))

        assert posts == []
