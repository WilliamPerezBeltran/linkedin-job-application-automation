"""Exceptions that are part of `LLMProvider`'s informal contract.

Ver `docs/decisions/006-llm-and-cv-exception-boundaries.md` (ADR-006) para
la decisión completa. Resumen: `AnalyzeJobPost`
(`app/application/use_cases/analyze_job_post.py`) y
`GenerateApplicationEmail`
(`app/application/use_cases/generate_application_email.py`) capturan estas
dos clases explícitamente (`except (LLMProviderError,
LLMResponseValidationError):`) para reintentar en una corrida futura en vez
de abortar el batch -- por eso viven acá, en `application/`, en vez de en
`app.infrastructure.llm.exceptions` (violaría el dependency rule,
`Infrastructure → Application interfaces → Domain`, nunca al revés).

`app.infrastructure.llm.exceptions` (ownership `llm-agent`) hereda de estas
clases -- no al revés. La raíz que agrupaba ambas en infraestructura
(`LLMInfrastructureError`) **no** se movió acá porque `application/` no la
importa hoy; queda como decisión de `llm-agent` conservarla como raíz
interna para excepciones que nunca cruzan este boundary, o retirarla.

Mismo criterio de "Protocol vive en `application/interfaces/`, sus
excepciones documentadas como parte del contrato lo acompañan" ya usado por
`LLMProvider` (`app/application/interfaces/llm_provider.py`, ADR-005).
"""

from __future__ import annotations


class LLMProviderError(Exception):
    """The call to the provider itself failed: timeout, rate limit, network
    error, authentication failure.

    `app.infrastructure.llm.exceptions.LLMProviderError` (ownership
    `llm-agent`) hereda de esta clase; el proveedor concreto
    (`OpenAIProvider`/`AnthropicProvider`) envuelve la excepción original
    del SDK vía `raise ... from exc`, nunca deja propagar el tipo crudo del
    SDK más allá de `app.infrastructure.llm`.
    """


class LLMResponseValidationError(Exception):
    """The provider responded, but its content did not validate against the
    expected Pydantic schema (missing field, wrong type, category outside
    the closed list, malformed JSON).

    `app.infrastructure.llm.exceptions.LLMResponseValidationError`
    (ownership `llm-agent`) hereda de esta clase; envuelve el
    `pydantic.ValidationError`/`json.JSONDecodeError` original vía
    `raise ... from exc`, sin dejar que esos tipos se filtren como contrato
    público de `LLMProvider`.
    """
