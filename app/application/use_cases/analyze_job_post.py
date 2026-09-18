"""`AnalyzeJobPost`: orchestrates a single Job Analyzer LLM run.

Toma un batch de `Job` en `SCRAPED` (`JobRepository.list_by_status`, ver
ADR-004 sección 1, `docs/decisions/004-job-pipeline-repositories.md`) y, para
cada uno:

1. Aplica el candidate filter barato por keywords
   (`app.application.services.candidate_filter.is_candidate_job_post`) antes
   de gastar un solo token — ver `TOKEN_OPTIMIZATION.md` §2. Un post que no
   pasa el filtro nunca llega a `LLMProvider.analyze_job()`. Este filtro vive
   en `application/services/` (no en `app/infrastructure/llm/`, donde
   `llm-agent` lo había colocado originalmente) porque es lógica pura sin
   ninguna dependencia de infraestructura — moverlo aquí resuelve una
   violación del dependency rule detectada por `code-reviewer` (Application
   no debe importar Infrastructure); ver el docstring del propio módulo para
   el razonamiento completo.
2. Si pasa el filtro, llama `LLMProvider.analyze_job(job.content)` y traduce
   el `JobAnalysisResult` devuelto a dominio exactamente como fija ADR-005
   sección 2 (`docs/decisions/005-llm-provider-interface.md`): `is_job=False`
   solo marca `NOT_RELEVANT`; `is_job=True` construye y persiste una
   `JobAnalysis` y marca `RELEVANT`. Si el resultado trae
   `email_addresses`, el primero se registra en el `Job` vía
   `Job.record_extracted_email` (`DEBT-ADR005-01`).

Decisiones de conteo y manejo de errores (ver docstring de `execute` y de
`AnalyzeJobPostResult` para el detalle completo):

- Los posts descartados por el candidate filter cuentan únicamente en
  `skipped_by_filter`, no en `analyzed`/`not_relevant` — así se puede medir
  por separado cuánto ahorra el filtro barato antes de tocar el LLM.
- Un fallo de infraestructura del proveedor (`LLMProviderError`,
  `LLMResponseValidationError`) deja el `Job` en `SCRAPED` sin marcarlo
  como analizado, para reintento en una corrida futura, y se cuenta en
  `failed_llm_calls` sin abortar el resto del batch.

No abre ni cierra transacción: recibe `JobRepository` y
`JobAnalysisRepository` ya construidos sobre la misma `Session` activa (ver
`session_scope()` en `app/infrastructure/database/session.py`) — mismo
criterio que `CollectFeedPosts`
(`app/application/use_cases/collect_feed_posts.py`). ADR-004 sección 2
("Nota de consistencia transaccional") exige explícitamente que, para
mantener atómica la persistencia de `Job` + `JobAnalysis`, ambos repositorios
concretos (`SQLAlchemyJobRepository`, `SQLAlchemyJobAnalysisRepository`)
compartan la misma `Session`, con un único `session.commit()` al final (a
cargo de quien construye este use case — el composition root del endpoint o
del scheduler), nunca un commit interno por cada llamada a `save()`.

`execute_one` (Fase 6, `backend-engineer`): la lógica que antes vivía
únicamente dentro del `for job in jobs:` de `execute()` se extrajo a este
método público, que procesa un único `Job` ya cargado por quien llama (el
composition root del endpoint `POST /api/jobs/{id}/analyze`,
`app/presentation/api/routes/jobs.py`, hace `job_repository.get_by_id(...)`
primero). `execute()` reusa `execute_one` en su loop -- sin duplicar
lógica. Diferencia clave respecto de `execute()`: `execute_one` **no**
atrapa `LLMProviderError`/`LLMResponseValidationError` -- las deja propagar,
para que el endpoint las traduzca a `502 Bad Gateway` (ver docstring de
`execute_one`); es `execute()` quien decide atraparlas para no abortar el
resto del batch. Tampoco valida el `status` del `Job` de antemano: esa
validación ya ocurre naturalmente dentro de `Job.mark_analyzed()` (lanza
`InvalidStateTransitionError` si el `Job` no está en `SCRAPED`), así que
agregar un chequeo redundante acá sería lógica de negocio duplicada fuera de
la entidad.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum

from app.application.interfaces.llm_provider import LLMProvider
from app.application.interfaces.llm_provider_errors import (
    LLMProviderError,
    LLMResponseValidationError,
)
from app.application.services.candidate_filter import is_candidate_job_post
from app.domain.entities.job import Job
from app.domain.entities.job_analysis import JobAnalysis
from app.domain.exceptions.invalid_email_address_error import InvalidEmailAddressError
from app.domain.repositories.job_analysis_repository import JobAnalysisRepository
from app.domain.repositories.job_repository import JobRepository
from app.domain.value_objects.email_address import EmailAddress
from app.domain.value_objects.job_status import JobStatus

logger = logging.getLogger(__name__)


class AnalyzeJobPostOutcome(StrEnum):
    """What `AnalyzeJobPost.execute_one` did with a single `Job`.

    - `RELEVANT`: el post pasó el candidate filter, el LLM respondió
      `is_job=True`, se persistió una `JobAnalysis` y el `Job` quedó en
      `RELEVANT`.
    - `NOT_RELEVANT`: el post pasó el candidate filter pero el LLM respondió
      `is_job=False`; el `Job` quedó en `NOT_RELEVANT` sin `JobAnalysis`.
    - `SKIPPED_BY_FILTER`: el post nunca llegó al LLM porque no pasó el
      candidate filter barato (`is_candidate_job_post`); el `Job` también
      quedó en `NOT_RELEVANT` (mismo status final que `NOT_RELEVANT`, pero
      distinguible acá para que `execute()` pueda seguir reportando
      `skipped_by_filter` por separado -- ver `AnalyzeJobPostResult`).

    Un fallo del proveedor LLM no es un `AnalyzeJobPostOutcome`: `execute_one`
    deja propagar `LLMProviderError`/`LLMResponseValidationError` en vez de
    devolver un tercer valor, para que quien llama decida qué hacer con la
    excepción (ver docstring de `execute_one`).
    """

    RELEVANT = "relevant"
    NOT_RELEVANT = "not_relevant"
    SKIPPED_BY_FILTER = "skipped_by_filter"


@dataclass(frozen=True, slots=True)
class AnalyzeJobPostResult:
    """Counters summarizing a single `AnalyzeJobPost` run.

    - `analyzed`: cuántos `Job` pasaron por `LLMProvider.analyze_job()` con
      éxito (sin importar si el resultado fue `RELEVANT`/`NOT_RELEVANT`).
      **No** incluye los descartados por `skipped_by_filter` -- ver ese
      campo para el razonamiento.
    - `relevant`: subconjunto de `analyzed` donde `JobAnalysisResult.is_job`
      fue `True` (se persistió una `JobAnalysis` y el `Job` quedó en
      `RELEVANT`).
    - `not_relevant`: subconjunto de `analyzed` donde
      `JobAnalysisResult.is_job` fue `False` (el `Job` quedó en
      `NOT_RELEVANT`, sin `JobAnalysis` creada).
    - `skipped_by_filter`: `Job` descartados por el candidate filter barato
      (`is_candidate_job_post`) antes de llamar al LLM. Se cuentan aparte de
      `analyzed`/`not_relevant` -- aunque terminan en el mismo status final
      (`NOT_RELEVANT`), separarlos permite medir cuánto ahorra el filtro por
      keywords en volumen de llamadas al proveedor, que es exactamente lo
      que `TOKEN_OPTIMIZATION.md` §2 pide poder observar. Si se sumaran a
      `not_relevant`, esa métrica de ahorro se perdería.
    - `failed_llm_calls`: `Job` para los que `LLMProvider.analyze_job()`
      lanzó `LLMProviderError`/`LLMResponseValidationError`. Quedan en
      `SCRAPED` (no se marcan como analizados) para reintento en una corrida
      futura -- no cuentan como `analyzed` ni como `not_relevant`, porque no
      es una decisión válida del pipeline sino un fallo transitorio de
      infraestructura.
    """

    analyzed: int
    relevant: int
    not_relevant: int
    skipped_by_filter: int
    failed_llm_calls: int


class AnalyzeJobPost:
    """Use case: run the Job Analyzer LLM over a batch of `SCRAPED` jobs."""

    def __init__(
        self,
        job_repository: JobRepository,
        job_analysis_repository: JobAnalysisRepository,
        llm_provider: LLMProvider,
    ) -> None:
        self._job_repository = job_repository
        self._job_analysis_repository = job_analysis_repository
        self._llm_provider = llm_provider

    def execute(self, *, limit: int = 50) -> AnalyzeJobPostResult:
        """Analyzes up to `limit` jobs currently in `SCRAPED`, oldest-first.

        Nunca reprocesa jobs que no estén en `SCRAPED`: eso ya lo garantiza
        `JobRepository.list_by_status` (ADR-004 sección 1). Un `Job` que
        falla por error de proveedor permanece en `SCRAPED` y puede volver a
        aparecer en una llamada futura a `execute()`.
        """
        jobs = self._job_repository.list_by_status(JobStatus.SCRAPED, limit=limit)

        analyzed = 0
        relevant = 0
        not_relevant = 0
        skipped_by_filter = 0
        failed_llm_calls = 0

        for job in jobs:
            try:
                outcome = self.execute_one(job)
            except (LLMProviderError, LLMResponseValidationError) as exc:
                # El Job permanece en SCRAPED para reintento -- no es un
                # resultado NOT_RELEVANT, es un fallo transitorio de
                # infraestructura. Nunca se loguea `job.content` completo
                # (higiene ya aplicada en `anthropic_provider.py`) ni la
                # excepción cruda del SDK subyacente.
                logger.warning(
                    "analyze_job_post.llm_call_failed job_id=%s error_type=%s",
                    job.id,
                    type(exc).__name__,
                )
                failed_llm_calls += 1
                continue

            if outcome is AnalyzeJobPostOutcome.SKIPPED_BY_FILTER:
                skipped_by_filter += 1
            elif outcome is AnalyzeJobPostOutcome.RELEVANT:
                analyzed += 1
                relevant += 1
            else:
                analyzed += 1
                not_relevant += 1

        return AnalyzeJobPostResult(
            analyzed=analyzed,
            relevant=relevant,
            not_relevant=not_relevant,
            skipped_by_filter=skipped_by_filter,
            failed_llm_calls=failed_llm_calls,
        )

    def execute_one(self, job: Job) -> AnalyzeJobPostOutcome:
        """Runs the Job Analyzer pipeline for a single, already-loaded `job`.

        Ver el docstring del módulo para el criterio completo. Resumen:

        - Nunca chequea `job.status` de antemano -- `Job.mark_analyzed()`
          lanza `InvalidStateTransitionError` si `job` no está en `SCRAPED`,
          y esa excepción se deja propagar sin atrapar (quien llama, p. ej.
          el endpoint, la traduce a `409 Conflict`).
        - `LLMProviderError`/`LLMResponseValidationError` (fallo transitorio
          del proveedor) también se dejan propagar sin atrapar -- a
          diferencia de `execute()`, que sí las atrapa para no abortar el
          resto del batch y las cuenta en `failed_llm_calls`. El endpoint
          que llame a este método directamente decide su propia traducción
          (p. ej. `502 Bad Gateway`).
        - Un email extraído inválido (`InvalidEmailAddressError`) sí se
          atrapa acá (no es un fallo del proveedor ni una decisión del
          pipeline, es un dato secundario descartable) -- mismo criterio que
          ya tenía `execute()`.
        """
        if not is_candidate_job_post(job.content):
            job.mark_analyzed()
            job.mark_not_relevant()
            self._job_repository.save(job)
            return AnalyzeJobPostOutcome.SKIPPED_BY_FILTER

        result = self._llm_provider.analyze_job(job.content)

        if result.email_addresses:
            try:
                job.record_extracted_email(EmailAddress(result.email_addresses[0]))
            except InvalidEmailAddressError:
                logger.warning(
                    "analyze_job_post.invalid_extracted_email job_id=%s",
                    job.id,
                )

        if result.is_job:
            job_analysis = JobAnalysis(
                job_id=job.id,
                job_type=result.job_type,
                seniority=result.seniority,
                skills=result.skills,
                languages=result.languages,
                frameworks=result.frameworks,
                cloud=result.cloud,
                ai_related=result.ai_related,
            )
            self._job_analysis_repository.save(job_analysis)
            job.mark_analyzed()
            job.mark_relevant()
            self._job_repository.save(job)
            return AnalyzeJobPostOutcome.RELEVANT

        job.mark_analyzed()
        job.mark_not_relevant()
        self._job_repository.save(job)
        return AnalyzeJobPostOutcome.NOT_RELEVANT
