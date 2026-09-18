"""`AnthropicProvider`: `LLMProvider` implementation backed by Anthropic's Claude.

Satisface `app.application.interfaces.llm_provider.LLMProvider` por
structural typing (no hereda del `Protocol`) — ver ADR-005
(`docs/decisions/005-llm-provider-interface.md`).

Configuración: lectura mínima y local del entorno (`os.environ`, cargado vía
`python-dotenv`), sin crear un `Settings` compartido nuevo — mismo criterio
pragmático que `app/infrastructure/database/session.py` usa para
`DATABASE_URL` (ese `Settings` tipado con `pydantic-settings` es ownership
de `backend-engineer` y todavía no existe).

Dos modelos distintos, uno por tarea, siguiendo `TOKEN_OPTIMIZATION.md` §6
("Job Analyzer... Modelo pequeño/rápido" / "Email Generator... Modelo
intermedio/grande, más exigente en calidad de texto"):

- `analyze_job`: familia Claude Haiku (barato/rápido) por default, siempre
  overrideable (constructor `model=` o env var `ANTHROPIC_MODEL`).
- `generate_email`: familia Claude Sonnet (calidad de redacción, tarea de
  menor volumen que el Analyzer) por default, siempre overrideable
  (constructor `email_model=` o env var `ANTHROPIC_EMAIL_MODEL`).

Ninguno de los dos queda hardcodeado sin forma de cambiarlo.

Salida estructurada: el SDK de Anthropic (a diferencia del "JSON mode"
nativo de OpenAI) no garantiza JSON válido por diseño para este tipo de
llamada simple sin herramientas; se le pide JSON explícitamente en el
`system prompt` (`prompts/job-analysis/v1.txt`) y se parsea de forma
estricta acá (`json.loads` + validación Pydantic), envolviendo cualquier
fallo en `LLMResponseValidationError`. (El SDK sí expone un `output_config`
de "structured outputs" más nuevo para algunos modelos/betas; se opta
deliberadamente por el enfoque prompt+parseo estricto, más simple, estable
entre modelos, y más fácil de fakear en tests sin depender de una feature en
evolución del proveedor.)
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Literal, Protocol

import anthropic
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.application.dto.email_context import EmailContext
from app.application.dto.generated_email import GeneratedEmail
from app.application.dto.job_analysis_result import JobAnalysisResult
from app.infrastructure.llm.exceptions import LLMProviderError, LLMResponseValidationError

load_dotenv()

# Familia barata/rápida sugerida por TOKEN_OPTIMIZATION.md §6 para el Analyzer
# (tarea de clasificación/extracción estructurada, no de redacción). Siempre
# overrideable vía constructor (`model=`) o `ANTHROPIC_MODEL`.
_DEFAULT_MODEL = "claude-3-5-haiku-latest"
# Familia intermedia/de mejor calidad de redacción, sugerida por
# TOKEN_OPTIMIZATION.md §6 para el Email Generator ("más exigente en calidad
# de texto... modelo intermedio/grande, ej. Sonnet") — tarea de menor volumen
# que el Analyzer, así que el costo extra por llamada es aceptable. Siempre
# overrideable vía constructor (`email_model=`) o `ANTHROPIC_EMAIL_MODEL`.
_DEFAULT_EMAIL_MODEL = "claude-3-5-sonnet-latest"
_DEFAULT_MAX_OUTPUT_TOKENS = 1000
_DEFAULT_MAX_RETRIES = 2
# Heurística gruesa (~4 caracteres por token) solo para fijar un techo
# defensivo de caracteres a partir de `LLM_MAX_INPUT_TOKENS` (que ya está en
# `.env.example` como límite en tokens, no en caracteres). El truncado "de
# negocio" real (~800-1000 caracteres, TOKEN_OPTIMIZATION.md §3) es
# responsabilidad de quien llama (`AnalyzeJobPost`); esto es solo una
# defensa barata adicional contra un input inesperadamente largo que ese
# caller haya dejado pasar.
_APPROX_CHARS_PER_TOKEN = 4
_DEFAULT_LLM_MAX_INPUT_TOKENS = 12000

_PROMPTS_ROOT = Path(__file__).resolve().parents[3] / "prompts"

_ANALYSIS_PROMPT_VERSION = "v1"
_ANALYSIS_PROMPT_PATH = _PROMPTS_ROOT / "job-analysis" / f"{_ANALYSIS_PROMPT_VERSION}.txt"

_EMAIL_PROMPT_VERSION = "v1"
_EMAIL_PROMPT_PATH = _PROMPTS_ROOT / "email-generation" / f"{_EMAIL_PROMPT_VERSION}.txt"

_JobCategory = Literal[
    "Java",
    "Python",
    "AI/ML",
    "Deep Learning",
    "JavaScript/Node",
    "Go",
    "Elixir",
    "Full Stack",
    "Other",
]

_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class _RawAnalysisResponse(BaseModel):
    """Internal Pydantic schema validating the raw JSON returned by Anthropic
    BEFORE it is translated into `JobAnalysisResult` (ADR-005, secciones 2 y 4).

    Nota deliberada de nombres: esta clase usa `job_category` (el vocabulario
    crudo del proveedor), no `job_type` (el vocabulario del dominio) — la
    traducción del campo ocurre en `AnthropicProvider.analyze_job`, nunca acá.
    """

    model_config = ConfigDict(extra="ignore")

    is_job: bool
    job_category: _JobCategory
    seniority: str | None = None
    skills: tuple[str, ...] = ()
    languages: tuple[str, ...] = ()
    frameworks: tuple[str, ...] = ()
    cloud: tuple[str, ...] = ()
    ai_related: bool = False
    email_addresses: tuple[str, ...] = ()
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("email_addresses")
    @classmethod
    def _drop_malformed_emails(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Descarta entradas sin forma de email en vez de invalidar toda la
        respuesta por un detalle de extracción menor: `is_job`/`job_category`/
        `confidence` son los campos que realmente gobiernan el ruteo del
        pipeline (ver ADR-005 sección 2), un email mal extraído no debería
        tirar abajo una clasificación por lo demás válida.
        """
        return tuple(email for email in value if _EMAIL_PATTERN.match(email))


class _RawEmailResponse(BaseModel):
    """Internal Pydantic schema validating the raw JSON returned by Anthropic
    for `generate_email`, BEFORE it is translated into `GeneratedEmail`
    (same criterion as `_RawAnalysisResponse` for `analyze_job`, ADR-005
    secciones 2-3).

    `subject`/`body` no vacíos (tras `strip()`): un asunto o cuerpo vacío no
    es un email de postulación utilizable — mismo espíritu que
    `ROADMAP.md` Fase 5 punto 2 ("no inventar... nada") aplicado del lado
    opuesto (no aceptar tampoco una respuesta vacía/inútil).
    """

    model_config = ConfigDict(extra="ignore")

    subject: str
    body: str

    @field_validator("subject", "body")
    @classmethod
    def _reject_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped


class _CompletionClient(Protocol):
    """Minimal seam between `AnthropicProvider` and the Anthropic SDK.

    Deliberadamente angosto (un solo método) para que los tests puedan
    inyectar un doble de prueba sin instanciar `anthropic.Anthropic` (sin
    red, sin API key, sin costo) y sin tener que pelear con `mypy --strict`
    contra la firma completa de `anthropic.Anthropic().messages.create`
    (muchos parámetros opcionales tipados con `Omit`/uniones complejas). El
    código de producción obtiene esto envolviendo el cliente real del SDK en
    `_AnthropicSDKClient` más abajo.
    """

    def complete(self, *, system_prompt: str, user_content: str, model: str) -> str:
        """Returns the raw text of the assistant's reply (text content blocks
        already concatenated).

        `model` se recibe por llamada (no se fija en la construcción del
        cliente) porque `AnthropicProvider` usa dos modelos distintos según
        la tarea (`analyze_job` vs `generate_email`, ver
        `TOKEN_OPTIMIZATION.md` §6) y comparte una única instancia de este
        cliente para ambas — ver módulo docstring.
        """
        ...


class _AnthropicSDKClient:
    """Adapts the real `anthropic.Anthropic` client to `_CompletionClient`."""

    def __init__(self, client: anthropic.Anthropic, *, max_output_tokens: int) -> None:
        self._client = client
        self._max_output_tokens = max_output_tokens

    def complete(self, *, system_prompt: str, user_content: str, model: str) -> str:
        message = self._client.messages.create(
            model=model,
            max_tokens=self._max_output_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_content}],
        )
        return "".join(
            block.text for block in message.content if isinstance(block, anthropic.types.TextBlock)
        )


def _load_system_prompt(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _extract_json_payload(raw_text: str) -> str:
    """Strips optional markdown code fences around a JSON payload.

    El prompt (`prompts/job-analysis/v1.txt`) ya pide explícitamente "sin
    bloques de markdown", pero algunos modelos igual envuelven la respuesta
    en ```json ... ``` — esta limpieza defensiva evita que eso, por sí solo,
    dispare un `LLMResponseValidationError` evitable.
    """
    text = raw_text.strip()
    if text.startswith("```"):
        text = text.removeprefix("```json").removeprefix("```")
        if text.endswith("```"):
            text = text[: -len("```")]
        text = text.strip()
    return text


def _build_email_user_content(context: EmailContext, *, job_content: str, cv_summary: str) -> str:
    """Serializes `EmailContext` into the user message sent to Anthropic for
    `generate_email`.

    `job_content`/`cv_summary` se reciben ya truncados por el caller
    (`AnthropicProvider.generate_email`) — esta función no aplica ningún
    límite propio, solo serializa. Formato de texto simple (no JSON) a
    propósito: es más económico en tokens que un JSON de input, y el prompt
    (`prompts/email-generation/v1.txt`) ya explica cómo interpretar cada
    bloque. Nunca se loguea el resultado de esta función (puede contener
    `job_content`/`cv_summary` con datos personales del post/CV) — ver
    `_complete`.
    """
    skills = ", ".join(context.skills) or "no especificadas"
    languages = ", ".join(context.languages) or "no especificados"
    frameworks = ", ".join(context.frameworks) or "no especificados"
    seniority = context.seniority or "no especificado"

    return (
        "Datos estructurados de la oferta:\n"
        f"- job_type: {context.job_type}\n"
        f"- seniority: {seniority}\n"
        f"- skills: {skills}\n"
        f"- languages: {languages}\n"
        f"- frameworks: {frameworks}\n"
        f"- author: {context.author}\n\n"
        "Texto original del post (job_content, puede estar truncado):\n"
        f'"""\n{job_content}\n"""\n\n'
        "Resumen del CV del candidato (cv_summary, única fuente válida de "
        "datos sobre su experiencia, puede estar truncado):\n"
        f'"""\n{cv_summary}\n"""\n'
    )


class AnthropicProvider:
    """`LLMProvider` implementation using Anthropic's Messages API.

    Ownership: `llm-agent` (`app/infrastructure/llm/**`). No hereda de
    `LLMProvider` (`Protocol`) — lo satisface por forma, mismo criterio que
    el resto de adaptadores de infraestructura de este proyecto.
    """

    def __init__(
        self,
        *,
        client: _CompletionClient | None = None,
        api_key: str | None = None,
        model: str | None = None,
        email_model: str | None = None,
        max_input_chars: int | None = None,
        max_output_tokens: int | None = None,
        max_retries: int | None = None,
    ) -> None:
        """Builds the provider.

        `client`: inyección de dependencia para tests (o para reusar un
        cliente ya configurado) — cuando se pasa, `api_key`/`max_retries` se
        ignoran porque el cliente ya está construido. Cuando es `None`
        (uso normal en producción), se construye un `anthropic.Anthropic`
        real a partir de `api_key`/`ANTHROPIC_API_KEY` y se envuelve en
        `_AnthropicSDKClient`. En ambos casos se resuelven dos modelos
        independientes (`model` para `analyze_job`, `email_model` para
        `generate_email`, ver `TOKEN_OPTIMIZATION.md` §6) porque el `client`
        (real o de test) es uno solo, compartido entre ambos métodos —
        `_CompletionClient.complete` recibe el modelo por llamada, no
        fijado en la construcción del cliente.
        """
        self._analysis_system_prompt = _load_system_prompt(_ANALYSIS_PROMPT_PATH)
        self._email_system_prompt = _load_system_prompt(_EMAIL_PROMPT_PATH)
        self._max_input_chars = max_input_chars or (
            int(os.environ.get("LLM_MAX_INPUT_TOKENS", str(_DEFAULT_LLM_MAX_INPUT_TOKENS)))
            * _APPROX_CHARS_PER_TOKEN
        )
        self._analysis_model = model or os.environ.get("ANTHROPIC_MODEL", _DEFAULT_MODEL)
        self._email_model = email_model or os.environ.get(
            "ANTHROPIC_EMAIL_MODEL", _DEFAULT_EMAIL_MODEL
        )

        if client is not None:
            self._client: _CompletionClient = client
            return

        resolved_api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not resolved_api_key:
            # Reutiliza `LLMProviderError` (no una tercera subclase, ver ADR-005
            # sección 4, "dos subclases, no más, por ahora") para "la llamada al
            # proveedor no pudo ni intentarse" — mismo bucket que timeout/rate
            # limit/auth failure: en los tres casos, la llamada no obtuvo
            # una respuesta utilizable del proveedor.
            raise LLMProviderError(
                "ANTHROPIC_API_KEY is not configured (set it in .env or pass api_key=...)."
            )

        resolved_max_output_tokens = max_output_tokens or int(
            os.environ.get("LLM_MAX_OUTPUT_TOKENS", str(_DEFAULT_MAX_OUTPUT_TOKENS))
        )
        resolved_max_retries = (
            max_retries
            if max_retries is not None
            else int(os.environ.get("LLM_MAX_RETRIES", str(_DEFAULT_MAX_RETRIES)))
        )

        sdk_client = anthropic.Anthropic(api_key=resolved_api_key, max_retries=resolved_max_retries)
        self._client = _AnthropicSDKClient(sdk_client, max_output_tokens=resolved_max_output_tokens)

    def analyze_job(self, content: str) -> JobAnalysisResult:
        """See `LLMProvider.analyze_job`. May raise `LLMProviderError` or
        `LLMResponseValidationError` (ver ADR-005 sección 4)."""
        truncated_content = content[: self._max_input_chars]

        raw_text = self._complete(
            truncated_content,
            system_prompt=self._analysis_system_prompt,
            model=self._analysis_model,
        )
        raw_response = self._parse_and_validate_analysis(raw_text)

        return JobAnalysisResult(
            is_job=raw_response.is_job,
            job_type=raw_response.job_category,
            seniority=raw_response.seniority,
            skills=raw_response.skills,
            languages=raw_response.languages,
            frameworks=raw_response.frameworks,
            cloud=raw_response.cloud,
            ai_related=raw_response.ai_related,
            email_addresses=raw_response.email_addresses,
            confidence=raw_response.confidence,
        )

    def generate_email(self, context: EmailContext) -> GeneratedEmail:
        """See `LLMProvider.generate_email`. May raise `LLMProviderError` or
        `LLMResponseValidationError` (ver ADR-005 sección 4).

        `context.job_content` y `context.cv_summary` se truncan acá
        defensivamente con el mismo límite (`self._max_input_chars`) que
        `analyze_job` usa para `content` — mismo criterio, ver comentario de
        `_DEFAULT_LLM_MAX_INPUT_TOKENS` más arriba. `cv_summary` se espera
        corto por diseño (3-5 líneas, `TOKEN_OPTIMIZATION.md` §7), pero es
        texto libre igual que `job_content`, así que recibe la misma defensa
        contra un input inesperadamente largo. El resto de los campos de
        `EmailContext` (tuplas/strings cortos ya estructurados) se
        serializan sin truncar.
        """
        truncated_job_content = context.job_content[: self._max_input_chars]
        truncated_cv_summary = context.cv_summary[: self._max_input_chars]
        user_content = _build_email_user_content(
            context, job_content=truncated_job_content, cv_summary=truncated_cv_summary
        )

        raw_text = self._complete(
            user_content, system_prompt=self._email_system_prompt, model=self._email_model
        )
        raw_response = self._parse_and_validate_email(raw_text)

        return GeneratedEmail(subject=raw_response.subject, body=raw_response.body)

    def _complete(self, content: str, *, system_prompt: str, model: str) -> str:
        try:
            return self._client.complete(
                system_prompt=system_prompt, user_content=content, model=model
            )
        except Exception as exc:
            # Nunca se loguea `content` completo acá (higiene de logs, ver
            # ADR-005 sección 4) — el mensaje es genérico a propósito.
            raise LLMProviderError("The call to the Anthropic provider failed.") from exc

    def _parse_and_validate_analysis(self, raw_text: str) -> _RawAnalysisResponse:
        try:
            payload = json.loads(_extract_json_payload(raw_text))
            return _RawAnalysisResponse.model_validate(payload)
        except (json.JSONDecodeError, ValidationError) as exc:
            # Nunca se loguea `raw_text` completo acá (puede contener texto
            # arbitrario del proveedor) — solo el tipo de fallo.
            raise LLMResponseValidationError(
                f"The Anthropic response failed schema validation ({type(exc).__name__})."
            ) from exc

    def _parse_and_validate_email(self, raw_text: str) -> _RawEmailResponse:
        try:
            payload = json.loads(_extract_json_payload(raw_text))
            return _RawEmailResponse.model_validate(payload)
        except (json.JSONDecodeError, ValidationError) as exc:
            # Nunca se loguea `raw_text` completo acá (puede contener texto
            # arbitrario del proveedor, potencialmente derivado del CV/post
            # del candidato) — solo el tipo de fallo.
            raise LLMResponseValidationError(
                f"The Anthropic response failed schema validation ({type(exc).__name__})."
            ) from exc
