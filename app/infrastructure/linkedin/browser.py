"""Playwright process/browser/context lifecycle — generic, sin ningún
conocimiento de LinkedIn (ni URLs, ni selectores, ni login). Eso vive en
`session.py`/`feed.py`. Mantener esta separación es lo que garantiza que
"Playwright es solo una implementación concreta" (`05_linkedin.md`) y que
ningún otro módulo de este paquete reimplementa el manejo de
`storage_state`.

Reutiliza `storage_state.json` (gitignored, ver `.gitignore`) para no
reautenticar en cada corrida (`ROADMAP.md` Fase 2 punto 1) — si el archivo
no existe todavía, se abre un contexto "limpio" (sin cookies), lo cual
`session.py` interpreta como "no hay sesión" y falla con
`LinkedInAuthenticationError` en vez de intentar loguearse solo.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from playwright.sync_api import Browser, BrowserContext, sync_playwright


@contextmanager
def launch_chromium(*, headless: bool) -> Iterator[Browser]:
    """Launches Chromium for the duration of the `with` block and closes it
    (and the underlying Playwright process) on exit, including on error.
    """
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless)
        try:
            yield browser
        finally:
            browser.close()


def open_context(browser: Browser, *, storage_state_path: Path) -> BrowserContext:
    """Opens a `BrowserContext`, loading `storage_state_path` if it exists.

    No decide si la sesión cargada sigue siendo válida — eso es
    responsabilidad de `session.verify_active_session`, que necesita un
    `Page` real para comprobarlo navegando al feed.
    """
    if storage_state_path.is_file():
        return browser.new_context(storage_state=str(storage_state_path))
    return browser.new_context()


def persist_storage_state(context: BrowserContext, *, storage_state_path: Path) -> None:
    """Writes the context's current cookies/local storage to
    `storage_state_path`, creating parent directories if needed.
    """
    storage_state_path.parent.mkdir(parents=True, exist_ok=True)
    context.storage_state(path=str(storage_state_path))
