"""`GenerateApplicationEmail`: orchestrates a single Email Generator LLM run
(Fase 5, ROADMAP.md).

Toma un batch de `Job` en `CV_SELECTED` (`JobRepository.list_by_status`, ver
ADR-004 sección 1, `docs/decisions/004-job-pipeline-repositories.md`) y, para
cada uno:

1. Busca su `JobAnalysis` asociado con `JobAnalysisRepository.get_by_job_id`.
   En un pipeline sano un `Job` en `CV_SELECTED` siempre tiene una
   `JobAnalysis` con `recommended_cv` ya seteado (lo garantiza `SelectBestCV`,
   `app/application/use_cases/select_best_cv.py`, Fase 4, en la misma pasada
   en la que marca `CV_SELECTED`) -- pero este use case no asume nada sobre
   el estado de infraestructura externa y se defiende de ese caso: si no
   existe, lo cuenta en `skipped_missing_analysis`, loguea un warning con el
   `job_id` (nunca contenido del post) y sigue con el resto del batch sin
   abortar. Mismo criterio defensivo que `SelectBestCV` con
   `skipped_missing_analysis`.
2. Resuelve el resumen de CV con `CVCatalog.get_summary(job_analysis.recommended_cv)`
   (`app/application/cv/cv_catalog.py`, ownership de `cv-matching-agent`, no
   tocado por este use case). Si el catálogo lanza cualquier
   `CVInfrastructureError` (p. ej. `CVNotFoundError` si el `cv_id` guardado ya
   no existe en `config/cvs.yaml`), se loguea un warning y se cuenta en
   `skipped_missing_cv_summary`, sin abortar el resto del batch -- análogo a
   un fallo transitorio de infraestructura, no a una decisión del pipeline.
3. Construye `EmailContext` con los campos ya detectados por el Analyzer
   (Fase 3) en la `JobAnalysis`, más `Job.author`/`Job.content` y el resumen
   de CV resuelto en el paso 2 -- ver ADR-005 sección 3
   (`docs/decisions/005-llm-provider-interface.md`) para el detalle de cada
   campo.
4. Llama a `LLMProvider.generate_email(context)`. Si lanza
   `LLMProviderError`/`LLMResponseValidationError`
   (`app/application/interfaces/llm_provider_errors.py`, ver ADR-006,
   `docs/decisions/006-llm-and-cv-exception-boundaries.md`): warning sin
   contenido sensible, `failed_llm_calls`, el `Job` permanece en
   `CV_SELECTED` para reintento futuro -- mismo criterio que
   `AnalyzeJobPost` con `failed_llm_calls`.
5. Si responde con éxito: registra `subject`/`body` en la `JobAnalysis` vía
   `record_generated_email` (exige `recommended_cv` ya seteado -- invariante
   garantizada por el paso 1/2, ver `app/domain/entities/job_analysis.py`),
   la persiste, marca el `Job` como `EMAIL_GENERATED` y lo persiste. Cuenta
   en `generated`.

No crea ninguna `Application` -- decisión ya fijada en ADR-004 sección 3
(`docs/decisions/004-job-pipeline-repositories.md`): el resultado de esta
fase se persiste únicamente en `JobAnalysis` (`subject`/`generated_email`).
`ApplicationRepository` recién se usa en Fase 7 (Gmail draft creation).

No abre ni cierra transacción: recibe `JobRepository` y
`JobAnalysisRepository` ya construidos sobre la misma `Session` activa (ver
`session_scope()` en `app/infrastructure/database/session.py`) -- mismo
criterio que `AnalyzeJobPost`/`SelectBestCV`. Un único `session.commit()` al
final, a cargo de quien construye este use case (el composition root del
endpoint o del scheduler).

`execute_one` (Fase 6, `backend-engineer`): mismo criterio de extracción que
`AnalyzeJobPost.execute_one`/`SelectBestCV.execute_one` -- la lógica que
antes vivía únicamente dentro del `for job in jobs:` de `execute()` se
extrajo a este método público, que procesa un único `Job` ya cargado por
quien llama (p. ej. el endpoint `POST /api/jobs/{id}/generate-email`,
`app/presentation/api/routes/jobs.py`). `execute()` reusa `execute_one` en
su loop, sin duplicar lógica. `execute_one` no chequea `job.status` de
antemano: `Job.mark_email_generated()` ya lanza
`InvalidStateTransitionError` si `job` no está en `CV_SELECTED`, y esa
excepción se deja propagar sin atrapar. `CVInfrastructureError` y
`LLMProviderError`/`LLMResponseValidationError` también se dejan propagar
sin atrapar -- a diferencia de `execute()`, que sí las atrapa para no
abortar el resto del batch y las cuenta en `skipped_missing_cv_summary`/
`failed_llm_calls` respectivamente.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum

from app.application.cv.cv_catalog import CVCatalog
from app.application.cv.cv_catalog_errors import CVInfrastructureError
from app.application.dto.email_context import EmailContext
from app.application.interfaces.llm_provider import LLMProvider
from app.application.interfaces.llm_provider_errors import (
    LLMProviderError,
    LLMResponseValidationError,
)
from app.domain.entities.job import Job
from app.domain.repositories.job_analysis_repository import JobAnalysisRepository
from app.domain.repositories.job_repository import JobRepository
from app.domain.value_objects.job_status import JobStatus

logger = logging.getLogger(__name__)


class GenerateApplicationEmailOutcome(StrEnum):
    """What `GenerateApplicationEmail.execute_one` did with a single `Job`.

    - `GENERATED`: el LLM respondió con éxito; `subject`/`body` quedaron
      registrados en la `JobAnalysis` y el `Job` pasó a `EMAIL_GENERATED`.
    - `MISSING_ANALYSIS`: el `Job` no tenía ninguna `JobAnalysis` asociada
      (caso defensivo, no debería ocurrir en un pipeline sano -- ver
      docstring del módulo, punto 1); no se intentó ninguna generación.

    Ni un resumen de CV faltante (`CVInfrastructureError`) ni un fallo del
    proveedor LLM (`LLMProviderError`/`LLMResponseValidationError`) son un
    `GenerateApplicationEmailOutcome`: `execute_one` deja propagar ambas
    excepciones en vez de devolver un valor adicional, para que quien llama
    decida qué hacer (ver docstring de `execute_one`).
    """

    GENERATED = "generated"
    MISSING_ANALYSIS = "missing_analysis"


@dataclass(frozen=True, slots=True)
class GenerateApplicationEmailResult:
    """Counters summarizing a single `GenerateApplicationEmail` run.

    - `generated`: cuántos `Job` en `CV_SELECTED` recibieron un email
      generado con éxito (`LLMProvider.generate_email()` respondió). Su
      `JobAnalysis` quedó actualizada con `subject`/`generated_email` y el
      `Job` pasó a `EMAIL_GENERATED`.
    - `failed_llm_calls`: subconjunto del batch procesado (con `JobAnalysis`
      y resumen de CV disponibles) donde `LLMProvider.generate_email()`
      lanzó `LLMProviderError`/`LLMResponseValidationError`. El `Job`
      permanece en `CV_SELECTED` para reintento en una corrida futura -- no
      es una decisión válida del pipeline sino un fallo transitorio de
      infraestructura, mismo criterio que `AnalyzeJobPost.failed_llm_calls`.
    - `skipped_missing_analysis`: `Job` en `CV_SELECTED` sin ninguna
      `JobAnalysis` asociada -- caso defensivo que no debería ocurrir en un
      pipeline sano (todo `Job` marcado `CV_SELECTED` por `SelectBestCV`
      tiene una `JobAnalysis` con `recommended_cv` seteado), pero se cuenta
      aparte en vez de abortar el batch. No se intenta ninguna generación
      para estos jobs.
    - `skipped_missing_cv_summary`: subconjunto del batch procesado (con
      `JobAnalysis` disponible) donde `CVCatalog.get_summary()` lanzó
      `CVInfrastructureError` (p. ej. el `cv_id` guardado ya no existe en
      `config/cvs.yaml`). El `Job` permanece en `CV_SELECTED` -- no se llama
      al LLM sin un resumen de CV resuelto.
    """

    generated: int
    failed_llm_calls: int
    skipped_missing_analysis: int
    skipped_missing_cv_summary: int


class GenerateApplicationEmail:
    """Use case: run the Email Generator LLM over a batch of `CV_SELECTED`
    jobs."""

    def __init__(
        self,
        job_repository: JobRepository,
        job_analysis_repository: JobAnalysisRepository,
        cv_catalog: CVCatalog,
        llm_provider: LLMProvider,
    ) -> None:
        self._job_repository = job_repository
        self._job_analysis_repository = job_analysis_repository
        self._cv_catalog = cv_catalog
        self._llm_provider = llm_provider

    def execute(self, *, limit: int = 50) -> GenerateApplicationEmailResult:
        """Generates an application email for up to `limit` jobs currently
        in `CV_SELECTED`, oldest-first.

        Nunca reprocesa jobs que no estén en `CV_SELECTED`: eso ya lo
        garantiza `JobRepository.list_by_status` (ADR-004 sección 1). Un
        `Job` que falla por error de proveedor o resumen de CV faltante
        permanece en `CV_SELECTED` y puede volver a aparecer en una llamada
        futura a `execute()`.
        """
        jobs = self._job_repository.list_by_status(JobStatus.CV_SELECTED, limit=limit)

        generated = 0
        failed_llm_calls = 0
        skipped_missing_analysis = 0
        skipped_missing_cv_summary = 0

        for job in jobs:
            try:
                outcome = self.execute_one(job)
            except CVInfrastructureError as exc:
                logger.warning(
                    "generate_application_email.missing_cv_summary job_id=%s error_type=%s",
                    job.id,
                    type(exc).__name__,
                )
                skipped_missing_cv_summary += 1
                continue
            except (LLMProviderError, LLMResponseValidationError) as exc:
                # El Job permanece en CV_SELECTED para reintento -- fallo
                # transitorio de infraestructura, no una decisión del
                # pipeline. Nunca se loguea el contenido del post/email
                # generado, solo el tipo de excepción.
                logger.warning(
                    "generate_application_email.llm_call_failed job_id=%s error_type=%s",
                    job.id,
                    type(exc).__name__,
                )
                failed_llm_calls += 1
                continue

            if outcome is GenerateApplicationEmailOutcome.MISSING_ANALYSIS:
                skipped_missing_analysis += 1
            else:
                generated += 1

        return GenerateApplicationEmailResult(
            generated=generated,
            failed_llm_calls=failed_llm_calls,
            skipped_missing_analysis=skipped_missing_analysis,
            skipped_missing_cv_summary=skipped_missing_cv_summary,
        )

    def execute_one(self, job: Job) -> GenerateApplicationEmailOutcome:
        """Runs the Email Generator pipeline for a single, already-loaded `job`.

        Ver el docstring del módulo para el criterio completo. No chequea
        `job.status` de antemano -- `Job.mark_email_generated()` lanza
        `InvalidStateTransitionError` si `job` no está en `CV_SELECTED`, y
        esa excepción se deja propagar sin atrapar.
        """
        job_analysis = self._job_analysis_repository.get_by_job_id(job.id)
        if job_analysis is None:
            logger.warning(
                "generate_application_email.missing_job_analysis job_id=%s",
                job.id,
            )
            return GenerateApplicationEmailOutcome.MISSING_ANALYSIS

        # `recommended_cv` no es `None` acá -- invariante garantizada por
        # `SelectBestCV` (Fase 4) para todo `Job` en `CV_SELECTED`.
        cv_summary = self._cv_catalog.get_summary(job_analysis.recommended_cv)  # type: ignore[arg-type]

        context = EmailContext(
            job_type=job_analysis.job_type,
            seniority=job_analysis.seniority,
            skills=job_analysis.skills,
            languages=job_analysis.languages,
            frameworks=job_analysis.frameworks,
            author=job.author,
            job_content=job.content,
            cv_summary=cv_summary,
        )

        result = self._llm_provider.generate_email(context)

        job_analysis.record_generated_email(subject=result.subject, body=result.body)
        self._job_analysis_repository.save(job_analysis)
        job.mark_email_generated()
        self._job_repository.save(job)
        return GenerateApplicationEmailOutcome.GENERATED
