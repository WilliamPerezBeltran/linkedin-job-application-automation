"""Infrastructure-level exceptions for `app.infrastructure.llm`.

Deliberadamente **no** heredan de `app.domain.exceptions.domain_error.DomainError`
(ver `docs/decisions/005-llm-provider-interface.md`, ADR-005, sección 4):
esa jerarquía está reservada a violaciones de invariantes de *negocio*
(`Job`, `JobAnalysis`, `EmailAddress`, etc.). Todo lo que ocurre en este
módulo es infraestructura no confiable — un proveedor LLM externo
(Anthropic/OpenAI), igual que Playwright/el DOM de LinkedIn lo es para
`app.infrastructure.linkedin.exceptions`.

Jerarquía actualizada por `docs/decisions/006-llm-and-cv-exception-boundaries.md`
(ADR-006): `LLMProviderError`/`LLMResponseValidationError` pasan a heredar
de las clases homónimas en
`app.application.interfaces.llm_provider_errors` (no de una raíz local)
porque `application/` las importa y captura hoy por nombre
(`AnalyzeJobPost`, `GenerateApplicationEmail`) — la dirección de
dependencia correcta es `Infrastructure → Application interfaces →
Domain`, nunca al revés; antes de este ADR ocurría lo contrario
(`application/` importaba estas dos clases directamente desde este
módulo).

Este módulo ya no define una raíz local (`LLMInfrastructureError`, que
existía antes de ADR-006): quedaba sin ningún propósito real una vez que
sus dos únicas subclases pasan a heredar de `application/` en vez de
heredar entre sí de una raíz interna — mantenerla habría dejado una clase
sin subclases reales, solo especulativa (YAGNI, `ENGINEERING_STANDARDS.md`
§"YAGNI"). Si en el futuro aparece una excepción de infraestructura LLM
que deliberadamente no deba cruzar hacia `application/` (p. ej. un fallo
interno específico de un SDK que un provider decida no exponer), se puede
reintroducir una raíz local en ese momento — no antes.
"""

from __future__ import annotations

from app.application.interfaces.llm_provider_errors import (
    LLMProviderError as _LLMProviderErrorBase,
)
from app.application.interfaces.llm_provider_errors import (
    LLMResponseValidationError as _LLMResponseValidationErrorBase,
)


class LLMProviderError(_LLMProviderErrorBase):
    """The call to the provider itself failed: timeout, rate limit, network
    error, authentication failure.

    Envuelve la excepción original del SDK de Anthropic (o cualquier otra
    excepción que ocurra durante la llamada, incluida una lanzada por un
    cliente inyectado en tests) vía `raise ... from exc`; nunca deja
    propagar el tipo crudo del SDK más allá de `app.infrastructure.llm`.
    """


class LLMResponseValidationError(_LLMResponseValidationErrorBase):
    """The provider responded, but its content did not validate against the
    expected Pydantic schema (missing field, wrong type, category outside
    the closed list, malformed JSON).

    Envuelve el `pydantic.ValidationError`/`json.JSONDecodeError` original
    vía `raise ... from exc`, sin dejar que esos tipos se filtren como
    contrato público de `LLMProvider`.
    """
