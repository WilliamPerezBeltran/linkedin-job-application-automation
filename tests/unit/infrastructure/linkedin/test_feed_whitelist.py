"""Unit tests for the `linkedin.com/feed/*` navigation whitelist in
`app.infrastructure.linkedin.feed`.

Estos tests cubren el requisito no negociable de `05_linkedin.md`: si
Playwright intenta navegar fuera de `/feed/*`, debe abortar con
`DisallowedNavigationError`, nunca seguir silenciosamente. No requieren un
navegador real — `assert_allowed_feed_url` es una función pura.
"""

from __future__ import annotations

import pytest

from app.infrastructure.linkedin.exceptions import DisallowedNavigationError
from app.infrastructure.linkedin.feed import assert_allowed_feed_url

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "url",
    [
        "https://www.linkedin.com/feed/",
        "https://linkedin.com/feed/",
        "https://www.linkedin.com/feed",
        "https://www.linkedin.com/feed/update/urn:li:activity:12345/",
    ],
)
def test_assert_allowed_feed_url_accepts_feed_urls(url: str) -> None:
    assert_allowed_feed_url(url)  # no debe lanzar


@pytest.mark.parametrize(
    "url",
    [
        "https://www.linkedin.com/in/someone/",  # perfil
        "https://www.linkedin.com/company/acme/",  # empresa
        "https://www.linkedin.com/jobs/view/123456/",  # oferta individual
        "https://www.linkedin.com/messaging/thread/abc/",  # mensajería
        "https://www.linkedin.com/search/results/people/",  # búsqueda
        "https://www.linkedin.com/feedback/",  # path parecido pero no es /feed
        "https://example.com/feed/",  # host externo
        "http://www.linkedin.com/feed/",  # scheme no https
    ],
)
def test_assert_allowed_feed_url_rejects_urls_outside_feed(url: str) -> None:
    with pytest.raises(DisallowedNavigationError):
        assert_allowed_feed_url(url)
