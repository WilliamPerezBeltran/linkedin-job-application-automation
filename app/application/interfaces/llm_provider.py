"""Port towards LLM providers (OpenAI/Anthropic), consumed by `AnalyzeJobPost`
(Fase 3) y `GenerateApplicationEmail` (Fase 5).

Ver `docs/decisions/005-llm-provider-interface.md` (ADR-005) para la
decisión completa (ubicación, firma de ambos métodos, tipos de retorno,
excepciones de infraestructura). Resumen:

- Ambos métodos se fijan juntos en este único `Protocol` desde ahora, aunque
  `generate_email` no se use hasta Fase 5 — ADR-005 sección "Migration
  impact" lo pide explícitamente así, para no romper el contrato entre
  fases.
- `Protocol` (no ABC), mismo criterio que `FeedCollector`
  (`app/application/interfaces/feed_collector.py`) y `JobRepository`
  (`app/domain/repositories/job_repository.py`): contratos hacia sistemas
  externos que un use case de `backend-engineer` orquesta. Las
  implementaciones concretas (`OpenAIProvider`/`AnthropicProvider`,
  ownership `llm-agent`) viven en `app/infrastructure/llm/` y satisfacen
  esta interfaz por structural typing — los SDKs de OpenAI/Anthropic nunca
  se filtran hacia `application/` ni `domain/`.
- Ambos métodos pueden propagar `LLMProviderError` (la llamada al proveedor
  falló: timeout, rate limit, red, autenticación) o
  `LLMResponseValidationError` (el proveedor respondió, pero el contenido no
  valida contra el schema Pydantic esperado) — definidas junto a este
  `Protocol` en `app/application/interfaces/llm_provider_errors.py` (ver
  `docs/decisions/006-llm-and-cv-exception-boundaries.md`, ADR-006).
  `app/infrastructure/llm/exceptions.py` (ownership `llm-agent`) define
  subclases homónimas que heredan de estas dos, para que
  `OpenAIProvider`/`AnthropicProvider` puedan seguir lanzándolas sin que
  `infrastructure/` cruce el dependency rule hacia `application/` en
  sentido inverso. Igual que `FeedCollector.collect()` en ADR-003, este
  `Protocol` no las declara por nombre en la firma (Python no tiene
  `raises` tipado): se documentan aquí como parte del contrato informal,
  sin que `application/` tenga que importarlas obligatoriamente (aunque
  puede capturarlas si necesita un manejo distinto por tipo).
"""

from __future__ import annotations

from typing import Protocol

from app.application.dto.email_context import EmailContext
from app.application.dto.generated_email import GeneratedEmail
from app.application.dto.job_analysis_result import JobAnalysisResult


class LLMProvider(Protocol):
    def analyze_job(self, content: str) -> JobAnalysisResult:
        """Analyzes a single job post and returns a structured `JobAnalysisResult`.

        `content` es el texto del post (posiblemente truncado, ver
        `TOKEN_OPTIMIZATION.md` §3) — el truncado/limpieza es responsabilidad
        de quien llama (`AnalyzeJobPost`), no de este Protocol.

        Deliberadamente devuelve un DTO (`JobAnalysisResult`), no la entidad
        de dominio `JobAnalysis`: `llm-agent` no debe conocer las
        invariantes de `JobAnalysis` (`match_score` en `[0,1]`, "no
        `generated_email` sin `recommended_cv`") ni manejar
        `InvalidDomainValueError` dentro de su propia capa de
        infraestructura. Ver ADR-005 sección 2.
        """
        ...

    def generate_email(self, context: EmailContext) -> GeneratedEmail:
        """Drafts a subject + body for a single application email.

        `context.cv_summary` es siempre un resumen corto ya redactado
        (`str`), nunca una ruta a PDF — requisito explícito de
        `TOKEN_OPTIMIZATION.md` §7. Ver ADR-005 sección 3 para el
        razonamiento completo de cada campo de `EmailContext`.
        """
        ...
