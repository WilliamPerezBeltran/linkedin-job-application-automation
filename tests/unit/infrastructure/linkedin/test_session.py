"""Unit tests for `app.infrastructure.linkedin.session`.

Cubre `verify_active_session`, `_drive_manual_login` y `_try_fill` -- las
tres funciones de este módulo que reciben un `Page` ya abierto como
parámetro, en vez de construir uno propio -- contra un `_FakePage`/
`_FakeLocator` double, nunca un navegador real ni `linkedin.com` real
(`05_linkedin.md`).

`run_manual_login` **no** se cubre acá a propósito: abre su propio
`Browser`/`BrowserContext` reales vía `browser.launch_chromium`/
`open_context` (sin ningún punto de inyección), mismo tipo de gap ya
documentado por `linkedin-agent` para `LinkedInFeedCollector.collect()`
(ver el docstring de `test_linkedin_feed_collector.py`: "requeriría un
navegador real, prohibido en tests") -- gap aceptado, ver el reporte de la
tarea de cobertura.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import cast

import pytest
from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from app.infrastructure.linkedin import session as session_module
from app.infrastructure.linkedin.config import LinkedInSessionConfig
from app.infrastructure.linkedin.exceptions import LinkedInAuthenticationError
from app.infrastructure.linkedin.session import (
    _drive_manual_login,
    _try_fill,
    verify_active_session,
)

pytestmark = pytest.mark.unit

_FEED_URL = "https://www.linkedin.com/feed/"


class _FakeLocator:
    def __init__(self, *, count: int = 0) -> None:
        self._count = count
        self.filled_values: list[str] = []
        self.first = self

    def count(self) -> int:
        return self._count

    def fill(self, value: str) -> None:
        self.filled_values.append(value)


@dataclass
class _FakePage:
    """Stand-in for `playwright.sync_api.Page` covering only what
    `session.py` uses: `.goto`, `.url`, `.locator`, `.wait_for_url`."""

    url: str
    locators_by_selector: dict[str, _FakeLocator] = field(default_factory=dict)
    wait_for_url_target: str | None = None
    wait_for_url_error: Exception | None = None
    goto_calls: list[str] = field(default_factory=list)

    def goto(self, url: str, *, wait_until: str) -> None:
        self.goto_calls.append(url)
        self.url = url

    def locator(self, selector: str) -> _FakeLocator:
        return self.locators_by_selector.get(selector, _FakeLocator(count=0))

    def wait_for_url(self, predicate: object, *, timeout: float) -> None:
        if self.wait_for_url_error is not None:
            raise self.wait_for_url_error
        if self.wait_for_url_target is not None:
            self.url = self.wait_for_url_target


def _as_page(page: _FakePage) -> Page:
    """See `test_feed_navigation.py::_as_page` for why this cast (not a
    `# type: ignore` per call site) is needed against `mypy --strict`."""
    return cast(Page, page)


def _config(**overrides: object) -> LinkedInSessionConfig:
    defaults: dict[str, object] = {"login_timeout_seconds": 60}
    defaults.update(overrides)
    return LinkedInSessionConfig(**defaults)  # type: ignore[arg-type]


class TestVerifyActiveSession:
    def test_does_not_raise_when_the_session_ends_up_on_the_feed(self) -> None:
        page = _FakePage(url="about:blank")

        def _goto_to_feed(url: str, *, wait_until: str) -> None:
            page.goto_calls.append(url)
            page.url = _FEED_URL

        page.goto = _goto_to_feed  # type: ignore[method-assign]

        verify_active_session(_as_page(page), feed_url=_FEED_URL)

    def test_raises_authentication_error_when_the_page_did_not_end_up_on_the_feed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`navigate_to_feed` (`feed.py`) already enforces its own strict
        `linkedin.com/feed/*` whitelist on `page.url` *before*
        `verify_active_session` gets a chance to interpret the result --
        under the real whitelist, any URL that survives that check also
        satisfies this function's own (weaker, prefix-only) check, so this
        branch is effectively unreachable through the real `navigate_to_feed`
        with today's whitelist (worth a note back to `linkedin-agent`: this
        defensive check may be permanently dead code as currently written).
        `navigate_to_feed` is monkeypatched here to a no-op so this test can
        still verify `verify_active_session`'s own contract in isolation:
        "if navigation succeeded but `page.url` isn't the feed, raise
        `LinkedInAuthenticationError`" -- independent of whether `feed.py`'s
        stricter check would also have caught it first in production."""
        page = _FakePage(url="https://www.linkedin.com/login")
        monkeypatch.setattr(
            session_module, "navigate_to_feed", lambda page, *, feed_url: None
        )

        with pytest.raises(LinkedInAuthenticationError):
            verify_active_session(_as_page(page), feed_url=_FEED_URL)


class TestDriveManualLogin:
    def test_prefills_email_and_password_when_configured(self) -> None:
        email_locator = _FakeLocator(count=1)
        password_locator = _FakeLocator(count=1)
        page = _FakePage(
            url="about:blank",
            locators_by_selector={
                "input#username": email_locator,
                "input#password": password_locator,
            },
            wait_for_url_target=_FEED_URL,
        )
        config = _config(login_email="me@example.com", login_password="hunter2")

        _drive_manual_login(_as_page(page), config)

        assert email_locator.filled_values == ["me@example.com"]
        assert password_locator.filled_values == ["hunter2"]
        assert page.goto_calls == ["https://www.linkedin.com/login"]

    def test_never_fills_anything_when_no_credentials_are_configured(self) -> None:
        email_locator = _FakeLocator(count=1)
        page = _FakePage(
            url="about:blank",
            locators_by_selector={"input#username": email_locator},
            wait_for_url_target=_FEED_URL,
        )
        config = _config(login_email=None, login_password=None)

        _drive_manual_login(_as_page(page), config)

        assert email_locator.filled_values == []

    def test_raises_authentication_error_when_login_times_out(self) -> None:
        page = _FakePage(
            url="about:blank",
            wait_for_url_error=PlaywrightTimeoutError("timed out"),
        )
        config = _config(login_timeout_seconds=1)

        with pytest.raises(LinkedInAuthenticationError, match="did not reach"):
            _drive_manual_login(_as_page(page), config)


class TestTryFill:
    def test_fills_the_field_when_the_locator_is_found(self) -> None:
        locator = _FakeLocator(count=1)
        page = _FakePage(url="about:blank", locators_by_selector={"input#username": locator})

        _try_fill(_as_page(page), "input#username", "me@example.com")

        assert locator.filled_values == ["me@example.com"]

    def test_does_nothing_when_the_locator_is_not_found(self) -> None:
        locator = _FakeLocator(count=0)
        page = _FakePage(url="about:blank", locators_by_selector={"input#username": locator})

        _try_fill(_as_page(page), "input#username", "me@example.com")

        assert locator.filled_values == []
