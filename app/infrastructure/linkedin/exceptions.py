"""Infrastructure-level exceptions for `app.infrastructure.linkedin`.

Deliberadamente **no** heredan de `app.domain.exceptions.domain_error.DomainError`
(ver ADR-003, sección 3): esa jerarquía está reservada a violaciones de
invariantes de *negocio* (`Job`, `EmailAddress`, etc.). Todo lo que ocurre
en este módulo es infraestructura no confiable (Playwright, el DOM real de
LinkedIn, la sesión del navegador) — nunca se confunde con una regla de
dominio.

Nombradas siguiendo la convención sugerida en `ENGINEERING_STANDARDS.md`
§10 (`LinkedInAuthenticationError`, `LinkedInNavigationError`,
`FeedScrapingError`) y el nombre orientativo de ADR-003 §3 para la
violación de whitelist (`DisallowedNavigationError`).
"""

from __future__ import annotations


class LinkedInInfrastructureError(Exception):
    """Root of every exception raised by `app.infrastructure.linkedin`.

    Nunca se lanza directamente — siempre una subclase concreta, para que
    quien la capture (p. ej. `CollectFeedPosts`, fuera de este ownership)
    pueda distinguir "sesión expirada" de "navegación bloqueada" de
    "LinkedIn nos mostró un checkpoint/CAPTCHA" sin recurrir a
    `except Exception`.
    """


class LinkedInAuthenticationError(LinkedInInfrastructureError):
    """No hay una sesión autenticada utilizable.

    Se lanza cuando no existe `storage_state.json`, o cuando la sesión
    persistida ya expiró (LinkedIn redirige a `/login` en vez de mostrar el
    feed). Nunca se resuelve reautenticando automáticamente con
    credenciales guardadas dentro de la misma corrida — la única forma de
    resolverla es que el usuario corra el flujo de login manual/controlado
    (`session.run_manual_login`) por separado, con un navegador visible.
    """


class LinkedInNavigationError(LinkedInInfrastructureError):
    """Raíz para cualquier problema de navegación dentro de LinkedIn."""


class DisallowedNavigationError(LinkedInNavigationError):
    """Se intentó navegar fuera de la whitelist `linkedin.com/feed/*`.

    Esta es la excepción específica exigida por el scope estricto del
    collector (perfiles, empresas, ofertas individuales, mensajería, etc.
    están prohibidos sin excepción) — nunca se silencia ni se continúa
    navegando tras capturarla.
    """

    def __init__(self, url: str) -> None:
        self.url = url
        super().__init__(
            f"Navigation to '{url}' is outside the linkedin.com/feed/* "
            "whitelist and was blocked before it happened."
        )


class LinkedInBlockedError(LinkedInInfrastructureError):
    """LinkedIn mostró un checkpoint/authwall/señal de bloqueo o CAPTCHA.

    Es el mecanismo de parada exigido por `05_linkedin.md` ("Seguridad") y
    `ROADMAP.md` Fase 2: ante esta señal el scraping se detiene por
    completo. Nunca se implementa lógica para resolver o evadir el
    challenge — eso queda, sin excepción, en manos del usuario.
    """


class FeedScrapingError(LinkedInInfrastructureError):
    """Error genérico al extraer/parsear el contenido del feed."""


class EmptyPostContentError(FeedScrapingError):
    """Un post del feed no tiene contenido de texto extraíble.

    A diferencia de otros campos (autor, timestamp, URL), el contenido es
    esencial: sin él no hay nada que el resto del pipeline (Job Analyzer)
    pueda evaluar, así que `parser.parse_post` falla de forma explícita en
    vez de devolver un `ParsedFeedPost` vacío/inútil.
    """
