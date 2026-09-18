"""Typed configuration for `app.infrastructure.linkedin`, read from
environment variables (`ENGINEERING_STANDARDS.md` §12: "toda configuración
debe estar fuera del código ... y una clase de configuración tipada").

No se reutiliza `app/infrastructure/config/` (vacío, Fase 0, sin ownership
asignado todavía) para no implementar fuera de
`app/infrastructure/linkedin/**` sin documentarlo — esta clase queda
deliberadamente local a este paquete; si en una fase posterior aparece un
`Settings` global, unificarla es responsabilidad de quien posea ese módulo
compartido, no de este agente.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

_DEFAULT_STORAGE_STATE_PATH = Path("storage_state.json")
_DEFAULT_FEED_URL = "https://www.linkedin.com/feed/"
_DEFAULT_LOGIN_TIMEOUT_SECONDS = 300
_DEFAULT_MAX_SCROLL_ITERATIONS = 5
_DEFAULT_SCROLL_DELAY_SECONDS = 2.0
_DEFAULT_MAX_POSTS = 20


@dataclass(frozen=True, slots=True)
class LinkedInSessionConfig:
    """Parámetros de sesión/navegación/rate-limiting del collector.

    - `login_email`/`login_password`: **opcionales**, solo usados por
      `session.run_manual_login` para pre-rellenar el formulario de login
      y ahorrarle tecleo al usuario — nunca para completar un login
      autónomo ni para saltarse un CAPTCHA/2FA. El usuario siempre debe
      confirmar/terminar el login a mano en la ventana visible del
      navegador. Nunca se guardan en la base de datos ni se loguean.
    - `max_scroll_iterations`/`scroll_delay_seconds`: rate limiting del
      scroll del feed — controlado y acotado, nunca scroll agresivo o
      continuo (`05_linkedin.md`).
    - `max_posts`: techo duro de posts extraídos por corrida, `None` para
      no acotar (no recomendado en producción).
    """

    storage_state_path: Path = _DEFAULT_STORAGE_STATE_PATH
    headless: bool = True
    login_email: str | None = None
    login_password: str | None = None
    login_timeout_seconds: int = _DEFAULT_LOGIN_TIMEOUT_SECONDS
    feed_url: str = _DEFAULT_FEED_URL
    max_scroll_iterations: int = _DEFAULT_MAX_SCROLL_ITERATIONS
    scroll_delay_seconds: float = _DEFAULT_SCROLL_DELAY_SECONDS
    max_posts: int | None = _DEFAULT_MAX_POSTS


def load_config_from_env() -> LinkedInSessionConfig:
    """Builds `LinkedInSessionConfig` from environment variables.

    Todas las variables son opcionales y caen a los defaults de arriba si
    no están definidas. `LINKEDIN_EMAIL`/`LINKEDIN_PASSWORD` ya están
    documentadas en `.env.example`.
    """
    return LinkedInSessionConfig(
        storage_state_path=Path(
            os.getenv("LINKEDIN_STORAGE_STATE_PATH", str(_DEFAULT_STORAGE_STATE_PATH))
        ),
        headless=_parse_bool(os.getenv("LINKEDIN_HEADLESS"), default=True),
        login_email=os.getenv("LINKEDIN_EMAIL") or None,
        login_password=os.getenv("LINKEDIN_PASSWORD") or None,
        login_timeout_seconds=int(
            os.getenv("LINKEDIN_LOGIN_TIMEOUT_SECONDS", str(_DEFAULT_LOGIN_TIMEOUT_SECONDS))
        ),
        feed_url=os.getenv("LINKEDIN_FEED_URL", _DEFAULT_FEED_URL),
        max_scroll_iterations=int(
            os.getenv("LINKEDIN_MAX_SCROLL_ITERATIONS", str(_DEFAULT_MAX_SCROLL_ITERATIONS))
        ),
        scroll_delay_seconds=float(
            os.getenv("LINKEDIN_SCROLL_DELAY_SECONDS", str(_DEFAULT_SCROLL_DELAY_SECONDS))
        ),
        max_posts=_parse_optional_int(os.getenv("LINKEDIN_MAX_POSTS", str(_DEFAULT_MAX_POSTS))),
    )


def _parse_bool(value: str | None, *, default: bool) -> bool:
    if value is None or not value.strip():
        return default
    return value.strip().lower() not in {"0", "false", "no"}


def _parse_optional_int(value: str | None) -> int | None:
    if value is None or not value.strip():
        return None
    return int(value)
