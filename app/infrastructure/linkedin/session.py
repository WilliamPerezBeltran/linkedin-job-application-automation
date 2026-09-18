"""LinkedIn session lifecycle: verificar que una sesión persistida
(`storage_state.json`) sigue activa, y — como paso *separado*, disparado
explícitamente por el usuario, nunca automáticamente dentro de
`LinkedInFeedCollector.collect()` — un flujo de login inicial
manual/controlado.

Decisión de diseño explícita (ver también "Seguridad" en
`05_linkedin.md`: *"Preferir sesión persistente ... o autenticación manual
sobre volver a autenticar con credenciales guardadas en cada corrida"*):

- `verify_active_session` **nunca** rellena ni envía el formulario de
  login. Si la sesión no es válida, falla con `LinkedInAuthenticationError`
  — la única forma de resolverlo es que el usuario corra
  `run_manual_login` por separado, con un navegador visible (`headless`
  forzado a `False` sin importar `config.headless`, porque un login
  headless no se puede completar a mano).
- `run_manual_login` puede pre-rellenar email/password desde
  `LINKEDIN_EMAIL`/`LINKEDIN_PASSWORD` (si están definidas) solo para
  ahorrar tecleo, pero nunca hace submit por su cuenta ni espera menos de
  que la navegación llegue de verdad a `/feed/` — el usuario completa el
  login (incluyendo cualquier CAPTCHA/2FA) a mano. No hay ningún camino de
  código que intente resolver o evadir un challenge.
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from app.infrastructure.linkedin.browser import (
    launch_chromium,
    open_context,
    persist_storage_state,
)
from app.infrastructure.linkedin.config import LinkedInSessionConfig
from app.infrastructure.linkedin.exceptions import LinkedInAuthenticationError
from app.infrastructure.linkedin.feed import navigate_to_feed

logger = logging.getLogger(__name__)

_LOGIN_URL = "https://www.linkedin.com/login"
_FEED_PATH_PREFIX = "/feed"
_EMAIL_INPUT_SELECTOR = "input#username"
_PASSWORD_INPUT_SELECTOR = "input#password"


def verify_active_session(page: Page, *, feed_url: str) -> None:
    """Navigates once to `feed_url` and raises `LinkedInAuthenticationError`
    unless the resulting page is actually the feed (i.e. the persisted
    session, if any, is still authenticated).

    Reutiliza `feed.navigate_to_feed` (whitelist + detección de
    checkpoint/bloqueo) — esta función solo añade la interpretación
    "sesión inválida" sobre el resultado.
    """
    navigate_to_feed(page, feed_url=feed_url)
    if not urlparse(page.url).path.startswith(_FEED_PATH_PREFIX):
        raise LinkedInAuthenticationError(
            f"No active LinkedIn session (ended up at '{page.url}' instead "
            "of the feed). Run the manual login step "
            "(`app.infrastructure.linkedin.session.run_manual_login`) once, "
            "in a visible browser, to (re)create storage_state.json."
        )
    logger.info("linkedin.session.active")


def run_manual_login(config: LinkedInSessionConfig) -> None:
    """Standalone bootstrap step: opens a **visible** browser on the login
    page, optionally pre-fills email/password from `config`, and waits for
    the user to finish logging in by hand (including any 2FA/CAPTCHA
    challenge) until the browser reaches `/feed/`. On success, persists
    `storage_state.json` for `LinkedInFeedCollector` to reuse.

    Intencionalmente **no** se llama desde `LinkedInFeedCollector.collect()`
    — es un paso separado que el usuario dispara explícitamente (p. ej.
    `python -m app.infrastructure.linkedin.session`) la primera vez, o
    cuando la sesión expiró.
    """
    with launch_chromium(headless=False) as browser:
        context = open_context(browser, storage_state_path=config.storage_state_path)
        try:
            page = context.new_page()
            try:
                _drive_manual_login(page, config)
            finally:
                page.close()
        finally:
            persist_storage_state(context, storage_state_path=config.storage_state_path)
            context.close()


def _drive_manual_login(page: Page, config: LinkedInSessionConfig) -> None:
    page.goto(_LOGIN_URL, wait_until="domcontentloaded")

    if config.login_email:
        _try_fill(page, _EMAIL_INPUT_SELECTOR, config.login_email)
    if config.login_password:
        _try_fill(page, _PASSWORD_INPUT_SELECTOR, config.login_password)

    logger.info(
        "linkedin.session.manual_login_required",
        extra={"timeout_seconds": config.login_timeout_seconds},
    )
    try:
        page.wait_for_url(
            lambda url: urlparse(url).path.startswith(_FEED_PATH_PREFIX),
            timeout=config.login_timeout_seconds * 1000,
        )
    except PlaywrightTimeoutError as exc:
        raise LinkedInAuthenticationError(
            "Login did not reach linkedin.com/feed/ within "
            f"{config.login_timeout_seconds}s. Complete the login manually "
            "(including any 2FA/CAPTCHA) and retry — automated bypass of "
            "any challenge is not implemented and must never be added."
        ) from exc


def _try_fill(page: Page, selector: str, value: str) -> None:
    """Best-effort pre-fill: if LinkedIn changed the login form markup, this
    silently does nothing rather than crashing the whole bootstrap flow —
    the user can always type the field by hand in the visible browser.
    """
    locator = page.locator(selector)
    if locator.count() > 0:
        locator.first.fill(value)


def _bootstrap_from_env() -> None:  # pragma: no cover - thin CLI entry point
    from app.infrastructure.linkedin.config import load_config_from_env

    run_manual_login(load_config_from_env())


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO)
    _bootstrap_from_env()
