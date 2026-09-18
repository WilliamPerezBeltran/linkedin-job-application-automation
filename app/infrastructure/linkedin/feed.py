"""Feed-only navigation and raw DOM extraction, guarded by an explicit URL
whitelist verified programmatically before every navigation — nunca se
confía solamente en "no hacer click" por convención (`05_linkedin.md`,
ADR-003 §3).

Scope estricto: `linkedin.com/feed/*` únicamente. Prohibido sin excepción
navegar a perfiles, páginas de empresa, ofertas individuales, links
externos, mensajería, likes, comentarios, conexiones, o búsquedas — este
módulo nunca hace `click` sobre un post ni sigue ningún link, solo lee el
DOM ya renderizado del feed.
"""

from __future__ import annotations

import logging
import posixpath
import time
from urllib.parse import urlparse

from playwright.sync_api import Page

from app.infrastructure.linkedin.config import LinkedInSessionConfig
from app.infrastructure.linkedin.exceptions import DisallowedNavigationError, LinkedInBlockedError

logger = logging.getLogger(__name__)

_ALLOWED_HOSTS = frozenset({"www.linkedin.com", "linkedin.com"})
_ALLOWED_PATH = "/feed"

# Marcadores de checkpoint/authwall/bloqueo de LinkedIn. Si aparecen en la
# URL tras una navegación, es señal de CAPTCHA, 2FA forzado, o rate-limit —
# se aborta de inmediato (`LinkedInBlockedError`), nunca se intenta evadir.
_BLOCK_MARKERS = ("/checkpoint/", "/authwall", "/uas/login")

_POST_CONTAINER_SELECTOR = "div.feed-shared-update-v2"


def assert_allowed_feed_url(url: str) -> None:
    """Raises `DisallowedNavigationError` unless `url` is within
    `linkedin.com/feed/*` (scheme `https`, host `linkedin.com` o
    `www.linkedin.com`, path `/feed` o `/feed/...`).

    Debe llamarse antes de *cualquier* navegación que este paquete
    realice hacia el feed. Deliberadamente estricto con el `path` (usa
    `== "/feed"` o `startswith("/feed/")`, no `startswith("/feed")`) para
    no aceptar por accidente rutas como `/feedback`. El path se normaliza
    con `posixpath.normpath` antes de comparar (defensa en profundidad:
    hoy los dos call sites reales pasan `config.feed_url` o `page.url` ya
    normalizado por el navegador, sin `..` literal, pero esta función debe
    seguir siendo segura si en el futuro se reutiliza para validar una URL
    construida a partir de un `href` scrapeado, no confiable).
    """
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = posixpath.normpath(parsed.path) if parsed.path else parsed.path
    allowed = (
        parsed.scheme == "https"
        and host in _ALLOWED_HOSTS
        and (path == _ALLOWED_PATH or path.startswith(_ALLOWED_PATH + "/"))
    )
    if not allowed:
        raise DisallowedNavigationError(url)


def navigate_to_feed(page: Page, *, feed_url: str) -> None:
    """Navigates to `feed_url` (whitelist-checked before and after)."""
    assert_allowed_feed_url(feed_url)
    page.goto(feed_url, wait_until="domcontentloaded")
    _assert_reachable(page.url)


def collect_raw_posts_html(page: Page, *, config: LinkedInSessionConfig) -> list[str]:
    """Scrolls the already-loaded feed page in small, rate-limited
    increments and returns the raw `innerHTML` of each distinct post
    container currently rendered.

    Nunca navega a otra URL, nunca hace `click` sobre un post ni sobre
    ningún link — solo lee el DOM y hace scroll. `config.max_scroll_iterations`
    y `config.scroll_delay_seconds` acotan el ritmo (rate limiting: nunca
    scroll agresivo o continuo). Se detiene de inmediato con
    `LinkedInBlockedError` si en cualquier punto LinkedIn muestra un
    checkpoint/bloqueo, y con `DisallowedNavigationError` si la página
    termina fuera de la whitelist (p. ej. una redirección inesperada).
    """
    _assert_reachable(page.url)

    seen_posts: dict[str, str] = {}
    for iteration in range(config.max_scroll_iterations):
        _assert_reachable(page.url)

        for element in page.query_selector_all(_POST_CONTAINER_SELECTOR):
            html = element.inner_html()
            seen_posts.setdefault(html, html)
            if config.max_posts is not None and len(seen_posts) >= config.max_posts:
                logger.info(
                    "linkedin.feed.max_posts_reached",
                    extra={"max_posts": config.max_posts, "iteration": iteration},
                )
                return list(seen_posts.values())

        page.mouse.wheel(0, 1800)
        time.sleep(config.scroll_delay_seconds)

    # Re-chequeo simétrico: la última iteración del loop hizo un scroll
    # después de su propio `_assert_reachable`, así que se vuelve a
    # verificar aquí antes de devolver el control al caller.
    _assert_reachable(page.url)
    return list(seen_posts.values())


def _assert_reachable(current_url: str) -> None:
    if any(marker in current_url for marker in _BLOCK_MARKERS):
        raise LinkedInBlockedError(
            f"LinkedIn presented a checkpoint/authwall/login redirect at "
            f"'{current_url}' (possible CAPTCHA, forced 2FA, or rate-limit "
            "block). Stopping — this must be resolved manually by the user "
            "in a visible browser; automated bypass is never implemented."
        )
    assert_allowed_feed_url(current_url)
