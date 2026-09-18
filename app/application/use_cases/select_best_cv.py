"""`SelectBestCV`: orchestrates a single CV Matcher run (Fase 4, ROADMAP.md).

Toma un batch de `Job` en `RELEVANT` (`JobRepository.list_by_status`, ver
ADR-004 sección 1, `docs/decisions/004-job-pipeline-repositories.md` --
`list_by_status` es el único método reutilizado por Analyzer/CV Matcher/
dashboard) y, para cada uno:

1. Busca su `JobAnalysis` asociado con `JobAnalysisRepository.get_by_job_id`.
   En un pipeline sano un `Job` en `RELEVANT` siempre tiene una `JobAnalysis`
   (la crea `AnalyzeJobPost`, `app/application/use_cases/analyze_job_post.py`,
   en la misma transacción en la que marca `RELEVANT`) -- pero este use case
   no asume nada sobre el estado de infraestructura externa y se defiende de
   ese caso: si no existe, lo cuenta en `skipped_missing_analysis`, loguea un
   warning con el `job_id` (nunca contenido del post) y sigue con el resto
   del batch sin abortar.
2. Si existe, llama a `CVMatcher.match(...)` (`app/application/cv/matcher.py`,
   ownership de `cv-matching-agent`, no tocado por este use case) con las
   skills de la `JobAnalysis`. Ver "Qué se pasa a `CVMatcher.match`" más
   abajo para el criterio exacto.
3. Si `CVMatchResult.recommended_cv` no es `None`: registra la recomendación
   en la `JobAnalysis` (`record_cv_recommendation`), la persiste, marca el
   `Job` como `CV_SELECTED` y lo persiste. Cuenta en `cv_selected`.
4. Si `CVMatchResult.recommended_cv` es `None` (ningún CV matchea ninguna
   skill): no se llama a `record_cv_recommendation` -- la entidad exige un
   `recommended_cv` no vacío (`JobAnalysis._require_non_empty`), no aceptaría
   `None` -- ni a `Job.mark_cv_selected()`. El `Job` permanece en `RELEVANT`.
   No existe un estado "CV no encontrado" en la máquina de estados actual
   (`app/domain/value_objects/job_status.py`); dejar el `Job` en `RELEVANT`
   es análogo a como `AnalyzeJobPost` deja jobs con fallo de LLM en `SCRAPED`
   para reintento -- acá, un `Job` sin match queda disponible para un futuro
   re-intento de este mismo use case si se agregan CVs nuevos al catálogo
   (`config/cvs.yaml`). Se cuenta en `no_match`.

Qué se pasa a `CVMatcher.match`
--------------------------------
Se usa únicamente `JobAnalysis.skills`, sin concatenar `languages`/
`frameworks`. Motivo: `skills` ya es el campo que el Job Analyzer (Fase 3,
`llm-agent`) puebla con la lista completa de tecnologías relevantes
detectadas en el post -- `languages`/`frameworks` son subconjuntos/vistas
más granulares de esa misma información (ver
`app/domain/entities/job_analysis.py` y `JobAnalysisResult`,
`app/application/dto/job_analysis_result.py`), no señales adicionales. El
propio ROADMAP (Fase 4, punto 4) pide explícitamente matchear "por
`skills`", y `docs/agents/AGENTS.md` sección 11 documenta el ejemplo
canónico (`matcher.match(job_analysis.skills)`) sin combinar campos.
Concatenar `languages`/`frameworks` no aportaría skills nuevas -- ya están
incluidas en `skills` -- pero sí arriesgaría inflar artificialmente el
`score`/`confidence` si el Analyzer alguna vez los pobla con texto que no
coincide 1:1 con lo que ya hay en `skills` (p. ej. variantes de
capitalización que `skill_normalizer` normaliza distinto). Mantenerlo simple
y ceñido al criterio explícito del ROADMAP evita esa clase de bug sutil
-- YAGNI (`docs/agents/AGENTS.md` principio 9).

No abre ni cierra transacción: recibe `JobRepository` y
`JobAnalysisRepository` ya construidos sobre la misma `Session` activa (ver
`session_scope()` en `app/infrastructure/database/session.py`) -- mismo
criterio que `AnalyzeJobPost`/`CollectFeedPosts`. Un único
`session.commit()` al final, a cargo de quien construye este use case (el
composition root del endpoint o del scheduler).

`execute_one` (Fase 6, `backend-engineer`): mismo criterio de extracción que
`AnalyzeJobPost.execute_one` -- la lógica que antes vivía únicamente dentro
del `for job in jobs:` de `execute()` se extrajo a este método público, que
procesa un único `Job` ya cargado por quien llama (p. ej. el endpoint `POST
/api/jobs/{id}/analyze`, `app/presentation/api/routes/jobs.py`, que encadena
`SelectBestCV.execute_one` inmediatamente después de que `AnalyzeJobPost`
deja un `Job` en `RELEVANT` -- ver el docstring de ese endpoint para el
razonamiento completo). `execute()` reusa `execute_one` en su loop, sin
duplicar lógica. `execute_one` no chequea `job.status` de antemano:
`Job.mark_cv_selected()` ya lanza `InvalidStateTransitionError` si `job` no
está en `RELEVANT`, y esa excepción se deja propagar sin atrapar.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum

from app.application.cv.matcher import CVMatcher
from app.domain.entities.job import Job
from app.domain.repositories.job_analysis_repository import JobAnalysisRepository
from app.domain.repositories.job_repository import JobRepository
from app.domain.value_objects.job_status import JobStatus

logger = logging.getLogger(__name__)


class SelectBestCVOutcome(StrEnum):
    """What `SelectBestCV.execute_one` did with a single `Job`.

    - `CV_SELECTED`: se encontró un CV con al menos una skill en común; se
      registró la recomendación en la `JobAnalysis` y el `Job` pasó a
      `CV_SELECTED`.
    - `NO_MATCH`: había `JobAnalysis`, pero ningún CV del catálogo comparte
      ninguna skill con el job; el `Job` permanece en `RELEVANT`.
    - `MISSING_ANALYSIS`: el `Job` no tenía ninguna `JobAnalysis` asociada
      (caso defensivo, no debería ocurrir en un pipeline sano -- ver
      docstring del módulo, punto 1); no se intentó ningún matching.
    """

    CV_SELECTED = "cv_selected"
    NO_MATCH = "no_match"
    MISSING_ANALYSIS = "missing_analysis"


@dataclass(frozen=True, slots=True)
class SelectBestCVResult:
    """Counters summarizing a single `SelectBestCV` run.

    - `cv_selected`: cuántos `Job` en `RELEVANT` recibieron una recomendación
      de CV con al menos una skill en común (`CVMatchResult.recommended_cv`
      no `None`). Su `JobAnalysis` quedó actualizada con
      `recommended_cv`/`match_score` y el `Job` pasó a `CV_SELECTED`.
    - `no_match`: subconjunto del batch procesado (con `JobAnalysis`
      disponible) donde `CVMatcher.match` no encontró ningún CV con al menos
      una skill en común (`recommended_cv is None`). El `Job` permanece en
      `RELEVANT` -- ver el docstring del módulo, punto 4, para el
      razonamiento completo de por qué no hay una transición de estado para
      este caso.
    - `skipped_missing_analysis`: `Job` en `RELEVANT` sin ninguna
      `JobAnalysis` asociada -- caso defensivo que no debería ocurrir en un
      pipeline sano (todo `Job` marcado `RELEVANT` por `AnalyzeJobPost` tiene
      una `JobAnalysis` creada en la misma pasada), pero se cuenta aparte en
      vez de abortar el batch. No se intenta ningún matching para estos
      jobs.
    """

    cv_selected: int
    no_match: int
    skipped_missing_analysis: int


class SelectBestCV:
    """Use case: run the deterministic CV Matcher over a batch of
    `RELEVANT` jobs."""

    def __init__(
        self,
        job_repository: JobRepository,
        job_analysis_repository: JobAnalysisRepository,
        cv_matcher: CVMatcher,
    ) -> None:
        self._job_repository = job_repository
        self._job_analysis_repository = job_analysis_repository
        self._cv_matcher = cv_matcher

    def execute(self, *, limit: int = 50) -> SelectBestCVResult:
        """Matches a CV for up to `limit` jobs currently in `RELEVANT`,
        oldest-first.

        Nunca reprocesa jobs que no estén en `RELEVANT`: eso ya lo garantiza
        `JobRepository.list_by_status` (ADR-004 sección 1). Un `Job` sin
        match razonable permanece en `RELEVANT` y puede volver a aparecer en
        una llamada futura a `execute()`.
        """
        jobs = self._job_repository.list_by_status(JobStatus.RELEVANT, limit=limit)

        cv_selected = 0
        no_match = 0
        skipped_missing_analysis = 0

        for job in jobs:
            outcome = self.execute_one(job)
            if outcome is SelectBestCVOutcome.CV_SELECTED:
                cv_selected += 1
            elif outcome is SelectBestCVOutcome.NO_MATCH:
                no_match += 1
            else:
                skipped_missing_analysis += 1

        return SelectBestCVResult(
            cv_selected=cv_selected,
            no_match=no_match,
            skipped_missing_analysis=skipped_missing_analysis,
        )

    def execute_one(self, job: Job) -> SelectBestCVOutcome:
        """Runs the CV Matcher for a single, already-loaded `job`.

        Ver el docstring del módulo para el criterio completo. No chequea
        `job.status` de antemano -- `Job.mark_cv_selected()` lanza
        `InvalidStateTransitionError` si `job` no está en `RELEVANT`, y esa
        excepción se deja propagar sin atrapar. `CVMatcher.match()` puede a
        su vez propagar `CVInfrastructureError`
        (`app.infrastructure.cv.exceptions`) si el catálogo de CVs no puede
        cargarse -- tampoco se atrapa acá, mismo criterio.
        """
        job_analysis = self._job_analysis_repository.get_by_job_id(job.id)
        if job_analysis is None:
            logger.warning(
                "select_best_cv.missing_job_analysis job_id=%s",
                job.id,
            )
            return SelectBestCVOutcome.MISSING_ANALYSIS

        result = self._cv_matcher.match(job_analysis.skills)

        if result.recommended_cv is None:
            return SelectBestCVOutcome.NO_MATCH

        job_analysis.record_cv_recommendation(
            recommended_cv=result.recommended_cv, match_score=result.confidence
        )
        self._job_analysis_repository.save(job_analysis)
        job.mark_cv_selected()
        self._job_repository.save(job)
        return SelectBestCVOutcome.CV_SELECTED
