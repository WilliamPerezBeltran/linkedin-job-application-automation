"""`GET/POST /api/jobs...`: Fase 6 (`ROADMAP.md`) -- API + dashboard de
revisión manual.

Endpoints expuestos (ver `docs/agents/AGENTS.md` sección 8 para la
superficie esperada del Backend Agent):

- `GET /api/jobs?status=...&limit=&offset=`: lista paginada de
  `JobSummaryResponse`, filtrada por `status` (requerido -- `JobRepository`
  solo soporta `list_by_status`, no "listar todo"; agregar ese método sin
  necesidad real violaría YAGNI, `docs/agents/AGENTS.md` principio 9).
- `GET /api/jobs/{id}`: `JobDetailResponse` con el detalle completo,
  incluyendo `matching_skills`/`missing_skills` calculados al vuelo (ver
  `_resolve_skill_match` más abajo).
- `POST /api/jobs/{id}/ignore`: transición manual a `IGNORED`.
- `POST /api/jobs/{id}/analyze`: corre `AnalyzeJobPost.execute_one`, con el
  encadenamiento de `SelectBestCV` documentado en `analyze_job` más abajo.
- `POST /api/jobs/{id}/generate-email`: corre
  `GenerateApplicationEmail.execute_one`.
- `PATCH /api/jobs/{id}/email`: edición manual del `subject`/`body`
  generado (ROADMAP Fase 6 punto 3), antes de crear el draft real de
  Gmail (Fase 7, fuera de esta fase). Usa `PATCH`, no `PUT`, porque el
  request (`EditGeneratedEmailRequest`) reemplaza únicamente
  `subject`/`body` de la `JobAnalysis` asociada -- una edición parcial de
  un recurso ya existente (`Job` + su `JobAnalysis`), no una sustitución
  completa de ese recurso ni una creación. Solo válido si el `Job` está en
  `EMAIL_GENERATED`: antes de eso no hay nada generado que editar
  (`POST .../generate-email` todavía no corrió), y desde `DRAFT_CREATED`
  en adelante ya existe un draft real en Gmail -- editar el `subject`/
  `body` ahí sin actualizar ese draft generaría una inconsistencia entre
  lo que el dashboard muestra y lo que Gmail ya tiene, y actualizar un
  draft existente es responsabilidad de la integración con Gmail (Fase 7),
  no de este endpoint. Esta restricción de status no es una transición de
  `Job.status` (editar el email no cambia el status), así que el chequeo
  vive en el propio handler, no en un nuevo método `mark_*` de la entidad
  `Job`.

Traducción de excepciones a HTTP (nunca se silencian, ver
`docs/agents/AGENTS.md` sección 8):

- `InvalidIdentifierError` (`{id}` no es un UUID válido) y "`Job` no
  existe" -> `404 Not Found`. Se tratan igual porque, desde la perspectiva
  del cliente de la API, ambos casos significan "ese recurso no existe" --
  no hay ninguna acción distinta que el cliente pueda tomar con un `422` en
  vez de un `404` acá.
- `InvalidStateTransitionError` (el `Job` no está en el status de origen que
  la operación requiere) -> `409 Conflict`.
- `LLMProviderError`/`LLMResponseValidationError`
  (`app/application/interfaces/llm_provider_errors.py`, ver ADR-006,
  `docs/decisions/006-llm-and-cv-exception-boundaries.md`) -> `502 Bad
  Gateway`, mismo criterio que `scrape.py` con errores de LinkedIn.
- `CVInfrastructureError` (`app/application/cv/cv_catalog_errors.py`, ver
  ADR-006) al resolver un resumen de CV o el catálogo completo -> `500
  Internal Server Error` genérico: no debería pasar si el `Job` ya tiene un
  `recommended_cv` válido de un catálogo consistente (invariante garantizada
  por `SelectBestCV`), así que es un error de infraestructura del servidor,
  no algo que el cliente pueda corregir con datos distintos.

**Fase 7 (segunda ronda), `POST /api/jobs/{id}/create-draft`:**

- `InvalidStateTransitionError` (el `Job` no está en `EMAIL_GENERATED`,
  chequeado por `CreateGmailDraft.execute_one` antes de tocar Gmail -- ver su
  docstring) -> `409 Conflict`, mismo criterio que `ignore_job`/`analyze_job`.
- `InconsistentJobPipelineStateError`
  (`app.application.use_cases.create_gmail_draft`, un `Job` en
  `EMAIL_GENERATED` sin la `JobAnalysis`/`email`/`recommended_cv` que ese
  status garantiza) -> `500 Internal Server Error`: es un bug del pipeline,
  no algo que el cliente pueda corregir con otro request -- mismo criterio
  que `CVInfrastructureError` más arriba.
- `CVInfrastructureError` (ADR-006), al resolver `cv_path` desde el catálogo
  -> `500 Internal Server Error`, mismo criterio ya usado en `generate_email`.
- `GmailInfrastructureError` (`app.infrastructure.gmail.exceptions`, raíz
  común de `GmailAuthenticationError`/`GmailCredentialsNotFoundError`/
  `GmailDraftCreationError`/`GmailAttachmentNotFoundError`/
  `GmailAttachmentPathTraversalError`) -> `502 Bad Gateway`, mismo criterio
  que `LLMProviderError` en `analyze_job`/`generate_email`: un fallo de un
  servicio externo (OAuth de Google, la API de Gmail, o el filesystem local
  donde vive el CV a adjuntar), no un dato del cliente inválido. Se captura
  la clase raíz (no cada subclase por separado, a diferencia de
  `LLMProviderError`/`LLMResponseValidationError` más arriba, que son dos
  clases sin relación de herencia entre sí con el mismo tratamiento HTTP):
  las seis subclases de `GmailInfrastructureError` comparten exactamente el
  mismo tratamiento acá (502 genérico, mismo mensaje, mismo log), y esa
  jerarquía ya existe deliberadamente para este propósito (ver su docstring
  -- "raíz abstracta + subclases concretas"), así que listar las seis por
  nombre solo agregaría acoplamiento a los detalles internos de
  `gmail-agent` sin ganar nada: si mañana se agrega una séptima subclase,
  este `except` la cubre automáticamente.
- `IntegrityError` de SQLAlchemy (constraint `uq_applications_job_id`, ver
  `SQLAlchemyApplicationRepository.save`) por dos requests concurrentes al
  mismo `Job` -- ambos pasan el chequeo de idempotencia de
  `CreateGmailDraft.execute_one` (`application_repository.get_by_job_id(job.id)
  is None`) antes de que cualquiera haga commit, ambos llaman a Gmail y crean
  un draft real, y el segundo `application_repository.save()` (dentro de
  `flush()`) pierde la carrera contra el constraint (hallazgo MEDIUM de
  `code-reviewer` sobre el cierre de Fases 7-10, corregido acá). Se maneja
  acá en el router, no dentro del use case, por dos motivos: (1) el use case
  (`app/application/use_cases/create_gmail_draft.py`) no conoce
  `sqlalchemy.exc.IntegrityError` -- es un detalle de infraestructura
  concreto, y hacerle `import` desde `app/application/` violaría la regla de
  dependencias (dominio/aplicación no dependen de infraestructura concreta,
  ver `docs/agents/AGENTS.md` sección 26); y (2) recuperarse de la carrera
  requiere un `session.rollback()` explícito sobre la `Session` cruda del
  request -- una vez que `flush()` lanza `IntegrityError`, esa `Session`
  queda inutilizable hasta el rollback, y el use case nunca recibe la
  `Session`, solo los repositorios ya construidos sobre ella (ADR-004). Tras
  el rollback, se vuelve a consultar `application_repository.get_by_job_id`
  sobre la misma `Session` (ya limpia): si la request concurrente ganó la
  carrera y ya comiteó, se devuelve esa `Application` existente como
  respuesta exitosa (200) -- mismo comportamiento que la idempotencia normal
  documentada arriba, la request que "pierde" la carrera termina viendo el
  mismo resultado que si hubiera llegado un poco más tarde y hubiera
  encontrado la `Application` ya creada. Si tras el rollback todavía no
  aparece ninguna `Application` (la otra transacción concurrente hizo
  `flush()` pero aún no comiteó -- ventana extremadamente angosta, no se
  reintenta en loop por YAGNI, ver docstring de `create_draft`) -> `409
  Conflict`, mismo código que el resto de las carreras de este tipo ya
  documentadas en el proyecto (`scrape.py`), indicando "hay una carrera en
  curso, reintentá" en vez de `500` (no es un bug del servidor, es una
  condición transitoria esperable bajo concurrencia real).

No abre transacciones manualmente: las dependencias de
`app/presentation/api/dependencies.py` (`get_job_repository`,
`get_job_analysis_repository`) envuelven `session_scope()` como una
dependencia FastAPI con `yield`, con un único commit al final del request --
ver el docstring de ese módulo para el razonamiento completo.

**Única excepción, documentada acá y en `analyze_job`:** `POST
/api/jobs/{id}/analyze` hace un `session.commit()` explícito e intermedio
entre `AnalyzeJobPost.execute_one` y el `SelectBestCV.execute_one`
encadenado (hallazgo HIGH de `code-reviewer` sobre esta misma fase,
corregido acá) -- sin ese checkpoint, un `CVInfrastructureError` en el paso
de CV Matcher hacía `rollback()` sobre **toda** la `Session` del request,
descartando también el análisis LLM ya persistido con éxito unos
milisegundos antes (una llamada real, pagada, al proveedor). Ver el
docstring de `analyze_job` para el detalle completo.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.application.cv.cv_catalog import CVCatalog
from app.application.cv.cv_catalog_errors import CVInfrastructureError
from app.application.cv.matcher import CVMatcher
from app.application.cv.skill_normalizer import normalize_skill
from app.application.dto.application_response import ApplicationResponse, build_gmail_url
from app.application.dto.edit_generated_email_request import EditGeneratedEmailRequest
from app.application.dto.job_detail_response import JobDetailResponse
from app.application.dto.job_summary_response import JobSummaryResponse
from app.application.interfaces.email_draft_repository import EmailDraftRepository
from app.application.interfaces.llm_provider import LLMProvider
from app.application.interfaces.llm_provider_errors import (
    LLMProviderError,
    LLMResponseValidationError,
)
from app.application.use_cases.analyze_job_post import AnalyzeJobPost, AnalyzeJobPostOutcome
from app.application.use_cases.create_gmail_draft import (
    CreateGmailDraft,
    InconsistentJobPipelineStateError,
)
from app.application.use_cases.generate_application_email import GenerateApplicationEmail
from app.application.use_cases.select_best_cv import SelectBestCV
from app.domain.entities.application import Application
from app.domain.entities.job import Job
from app.domain.entities.job_analysis import JobAnalysis
from app.domain.exceptions.invalid_identifier_error import InvalidIdentifierError
from app.domain.exceptions.invalid_state_transition_error import InvalidStateTransitionError
from app.domain.repositories.application_repository import ApplicationRepository
from app.domain.repositories.job_analysis_repository import JobAnalysisRepository
from app.domain.repositories.job_repository import JobRepository
from app.domain.value_objects.job_id import JobId
from app.domain.value_objects.job_status import JobStatus
from app.infrastructure.gmail.exceptions import GmailInfrastructureError
from app.presentation.api.dependencies import (
    get_application_repository,
    get_cv_catalog,
    get_cv_matcher,
    get_db_session,
    get_email_draft_repository,
    get_job_analysis_repository,
    get_job_repository,
    get_llm_provider,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

_JOB_NOT_FOUND_DETAIL = "Job not found."


def _parse_job_id(raw_job_id: str) -> JobId:
    """Parses a path `{id}` into a `JobId`, mapping a malformed UUID to 404.

    Ver el docstring del módulo -- un id sintácticamente inválido y un id
    bien formado que no existe se tratan igual desde la perspectiva del
    cliente de la API.
    """
    try:
        return JobId.of(raw_job_id)
    except InvalidIdentifierError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=_JOB_NOT_FOUND_DETAIL
        ) from exc


def _get_job_or_404(job_repository: JobRepository, job_id: JobId) -> Job:
    job = job_repository.get_by_id(job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_JOB_NOT_FOUND_DETAIL)
    return job


def _compute_skill_match(
    job_skills: Sequence[str], cv_skills: Sequence[str]
) -> tuple[list[str], list[str]]:
    """Computes `(matching_skills, missing_skills)` for the HTTP response only.

    Reimplementa, a propósito, el mismo algoritmo de intersección que
    `CVMatcher.match` (`app/application/cv/matcher.py`) ya usa para calcular
    `CVMatchResult.matching_skills`/`missing_skills` -- ese resultado no se
    persiste (`JobAnalysis` solo guarda `recommended_cv`/`match_score`, ver
    su docstring), así que `GET /api/jobs/{id}` no tiene forma de leerlo de
    vuelta y debe recalcularlo. Reusa `normalize_skill`
    (`app.application.cv.skill_normalizer`) para no duplicar la lógica de
    normalización -- solo se duplica acá el cruce de conjuntos, deliberado
    para no acoplar la capa de presentación a `CVMatcher`/`CVMatchResult`
    (un detalle interno de `app/application/cv/`, fuera del ownership de
    `backend-engineer`) ni forzar a `SelectBestCV` a persistir un campo que
    el dominio (`JobAnalysis`) no modela hoy.

    `matching_skills`: texto tal como aparece en `cv_skills` (lo que el CV
    puede demostrar). `missing_skills`: texto tal como aparece en
    `job_skills` (lo que la oferta pide y el CV no cubre). Mismo criterio de
    qué lado del texto se conserva que `CVMatchResult`.
    """
    distinct_job: dict[str, str] = {}
    for raw_skill in job_skills:
        if not raw_skill or not raw_skill.strip():
            continue
        key = normalize_skill(raw_skill)
        if key:
            distinct_job.setdefault(key, raw_skill)

    cv_normalized_keys = {
        key for raw_cv_skill in cv_skills if (key := normalize_skill(raw_cv_skill))
    }
    matched_keys = distinct_job.keys() & cv_normalized_keys

    seen_keys: set[str] = set()
    matching_skills: list[str] = []
    for raw_cv_skill in cv_skills:
        key = normalize_skill(raw_cv_skill)
        if key in matched_keys and key not in seen_keys:
            matching_skills.append(raw_cv_skill)
            seen_keys.add(key)

    missing_skills = [
        original for key, original in distinct_job.items() if key not in matched_keys
    ]

    return matching_skills, missing_skills


def _resolve_skill_match(
    job_analysis: JobAnalysis | None, cv_catalog: CVCatalog
) -> tuple[list[str], list[str]]:
    """Resolves `(matching_skills, missing_skills)` for `_to_detail`.

    Devuelve listas vacías si todavía no hay `JobAnalysis`/`recommended_cv`
    (CV Matcher no corrió). Si el catálogo de CVs falla al cargarse
    (`CVInfrastructureError`) se degrada a listas vacías con un warning en
    vez de tirar abajo la respuesta completa -- `matching_skills`/
    `missing_skills` son un dato explicativo adicional (ROADMAP Fase 6 punto
    2), no el contenido principal de la respuesta.
    """
    if job_analysis is None or job_analysis.recommended_cv is None:
        return [], []

    try:
        cv_profiles = cv_catalog.list_cvs()
    except CVInfrastructureError as exc:
        logger.warning(
            "jobs.skill_match.cv_catalog_unavailable recommended_cv=%s error_type=%s",
            job_analysis.recommended_cv,
            type(exc).__name__,
        )
        return [], []

    cv_profile = next(
        (profile for profile in cv_profiles if profile.id == job_analysis.recommended_cv), None
    )
    if cv_profile is None:
        return [], []

    return _compute_skill_match(job_analysis.skills, cv_profile.skills)


def _to_summary(job: Job, job_analysis: JobAnalysis | None) -> JobSummaryResponse:
    return JobSummaryResponse(
        id=str(job.id),
        author=job.author,
        url=job.url,
        status=job.status,
        published_at=job.published_at,
        scraped_at=job.scraped_at,
        job_type=job_analysis.job_type if job_analysis is not None else None,
        skills=list(job_analysis.skills) if job_analysis is not None else [],
        recommended_cv=job_analysis.recommended_cv if job_analysis is not None else None,
    )


def _to_detail(
    job: Job, job_analysis: JobAnalysis | None, cv_catalog: CVCatalog
) -> JobDetailResponse:
    summary = _to_summary(job, job_analysis)
    matching_skills, missing_skills = _resolve_skill_match(job_analysis, cv_catalog)
    return JobDetailResponse(
        **summary.model_dump(),
        seniority=job_analysis.seniority if job_analysis is not None else None,
        languages=list(job_analysis.languages) if job_analysis is not None else [],
        frameworks=list(job_analysis.frameworks) if job_analysis is not None else [],
        cloud=list(job_analysis.cloud) if job_analysis is not None else [],
        ai_related=job_analysis.ai_related if job_analysis is not None else None,
        matching_skills=matching_skills,
        missing_skills=missing_skills,
        subject=job_analysis.subject if job_analysis is not None else None,
        generated_email=job_analysis.generated_email if job_analysis is not None else None,
    )


@router.get("", response_model=list[JobSummaryResponse])
def list_jobs(
    status_filter: JobStatus = Query(
        ..., alias="status", description="Only jobs currently in this status are returned."
    ),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    job_repository: JobRepository = Depends(get_job_repository),
    job_analysis_repository: JobAnalysisRepository = Depends(get_job_analysis_repository),
) -> list[JobSummaryResponse]:
    jobs = job_repository.list_by_status(status_filter, limit=limit, offset=offset)
    return [_to_summary(job, job_analysis_repository.get_by_job_id(job.id)) for job in jobs]


@router.get("/{job_id}", response_model=JobDetailResponse)
def get_job(
    job_id: str,
    job_repository: JobRepository = Depends(get_job_repository),
    job_analysis_repository: JobAnalysisRepository = Depends(get_job_analysis_repository),
    cv_catalog: CVCatalog = Depends(get_cv_catalog),
) -> JobDetailResponse:
    job = _get_job_or_404(job_repository, _parse_job_id(job_id))
    job_analysis = job_analysis_repository.get_by_job_id(job.id)
    return _to_detail(job, job_analysis, cv_catalog)


@router.post("/{job_id}/ignore", response_model=JobSummaryResponse)
def ignore_job(
    job_id: str,
    job_repository: JobRepository = Depends(get_job_repository),
    job_analysis_repository: JobAnalysisRepository = Depends(get_job_analysis_repository),
) -> JobSummaryResponse:
    job = _get_job_or_404(job_repository, _parse_job_id(job_id))
    try:
        job.mark_ignored()
    except InvalidStateTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    job_repository.save(job)
    return _to_summary(job, job_analysis_repository.get_by_job_id(job.id))


@router.post("/{job_id}/analyze", response_model=JobDetailResponse)
def analyze_job(
    job_id: str,
    job_repository: JobRepository = Depends(get_job_repository),
    job_analysis_repository: JobAnalysisRepository = Depends(get_job_analysis_repository),
    llm_provider: LLMProvider = Depends(get_llm_provider),
    cv_matcher: CVMatcher = Depends(get_cv_matcher),
    cv_catalog: CVCatalog = Depends(get_cv_catalog),
    session: Session = Depends(get_db_session),
) -> JobDetailResponse:
    """Runs the Job Analyzer LLM for a single `Job`, then chains the CV
    Matcher automatically if the job ends up `RELEVANT`.

    Decisión de diseño (orchestrator, Fase 6) -- implementada tal cual, no
    rediseñada acá: `ROADMAP.md` Fase 6 no lista ningún endpoint explícito de
    "seleccionar CV" (a diferencia de `/analyze` y `/generate-email`), y
    `CLAUDE.md` describe el CV Matcher como un paso 100% automático/
    determinístico (sin LLM, sin necesidad de revisión humana) entre el
    Analyzer y el "Application Review Dashboard" -- a diferencia de la
    generación de email (LLM, sí requiere un botón explícito "Generate
    Email" en el frontend). Sin este encadenamiento, un `Job` `RELEVANT`
    nunca tendría `recommended_cv` para mostrar en el dashboard (ROADMAP
    Fase 6 punto 2: "lista de jobs... con... CV recomendado"), porque no
    existe ningún otro trigger para `SelectBestCV` en la superficie de esta
    fase. Por eso, si `AnalyzeJobPost.execute_one` deja el `Job` en
    `RELEVANT`, se llama inmediatamente `SelectBestCV.execute_one` sobre el
    mismo `job`/`job_repository`/`job_analysis_repository`.

    **Checkpoint de commit intermedio (corrección de un hallazgo HIGH de
    `code-reviewer` sobre esta misma fase):** entre ambos pasos se hace
    `session.commit()` explícito, en vez de dejar un único commit implícito
    al final del request (como sí hacen el resto de los endpoints de este
    router). Motivo: `AnalyzeJobPost.execute_one` y `SelectBestCV.execute_one`
    comparten la misma `Session` (ADR-004), pero son dos unidades de trabajo
    lógicamente independientes -- cada una ya es atómica *dentro* de sí
    misma (`Job`+`JobAnalysis` juntos), pero no hay ninguna invariante de
    negocio que exija que *ambas* tengan que confirmarse o revertirse juntas.
    Sin este commit intermedio, un `CVInfrastructureError` en el paso de CV
    Matcher (p. ej. `config/cvs.yaml` corrupto o inaccesible en ese momento)
    propagaba la excepción hasta `session_scope()` (vía la dependencia
    `yield` `get_db_session`), que hacía `rollback()` sobre **toda** la
    transacción del request -- incluyendo el análisis del LLM que
    `AnalyzeJobPost.execute_one` ya había completado y persistido con éxito
    unos milisegundos antes. Eso descartaba silenciosamente una llamada real
    (pagada, ver `TOKEN_OPTIMIZATION.md`) al proveedor LLM y revertía el
    `Job` a `SCRAPED` como si el análisis nunca hubiera ocurrido, obligando a
    pagar la misma llamada de nuevo en el reintento. Con el commit
    intermedio, un fallo del CV Matcher dejas el `Job` persistido en
    `RELEVANT` (reintentable por un futuro `SelectBestCV.execute()` batch o
    por un nuevo intento manual), consistente con el criterio que el resto
    del pipeline ya aplica: nunca perder trabajo ya exitoso por el fallo de
    un paso posterior.
    """
    job = _get_job_or_404(job_repository, _parse_job_id(job_id))

    analyze_use_case = AnalyzeJobPost(
        job_repository=job_repository,
        job_analysis_repository=job_analysis_repository,
        llm_provider=llm_provider,
    )
    try:
        outcome = analyze_use_case.execute_one(job)
    except InvalidStateTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except (LLMProviderError, LLMResponseValidationError) as exc:
        logger.warning(
            "jobs.analyze.llm_call_failed job_id=%s error_type=%s", job.id, type(exc).__name__
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="The Job Analyzer LLM call failed. Check server logs for details.",
        ) from exc

    # Checkpoint: confirma el trabajo de AnalyzeJobPost.execute_one antes de
    # intentar el paso encadenado de SelectBestCV -- ver docstring de esta
    # función para el razonamiento completo.
    session.commit()

    if outcome is AnalyzeJobPostOutcome.RELEVANT:
        select_cv_use_case = SelectBestCV(
            job_repository=job_repository,
            job_analysis_repository=job_analysis_repository,
            cv_matcher=cv_matcher,
        )
        try:
            select_cv_use_case.execute_one(job)
        except CVInfrastructureError as exc:
            logger.error(
                "jobs.analyze.cv_catalog_failed job_id=%s error_type=%s", job.id, type(exc).__name__
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to load the CV catalog while selecting a recommended CV.",
            ) from exc

    job_analysis = job_analysis_repository.get_by_job_id(job.id)
    return _to_detail(job, job_analysis, cv_catalog)


_EMAIL_NOT_EDITABLE_DETAIL = (
    "The generated email can only be edited while the job is in EMAIL_GENERATED "
    "(current status: {status})."
)
_JOB_ANALYSIS_NOT_FOUND_DETAIL = "No generated email found for this job."


@router.patch("/{job_id}/email", response_model=JobDetailResponse)
def edit_generated_email(
    job_id: str,
    request: EditGeneratedEmailRequest,
    job_repository: JobRepository = Depends(get_job_repository),
    job_analysis_repository: JobAnalysisRepository = Depends(get_job_analysis_repository),
    cv_catalog: CVCatalog = Depends(get_cv_catalog),
) -> JobDetailResponse:
    """Manually overwrites the generated `subject`/`body` for a `Job` that
    already went through `POST /{job_id}/generate-email`.

    Ver el docstring del módulo para el razonamiento completo de por qué
    `PATCH`, por qué solo se permite en `EMAIL_GENERATED`, y por qué este
    chequeo de status vive acá en vez de en un `mark_*` de `Job`.
    """
    job = _get_job_or_404(job_repository, _parse_job_id(job_id))
    job_analysis = job_analysis_repository.get_by_job_id(job.id)
    if job_analysis is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=_JOB_ANALYSIS_NOT_FOUND_DETAIL
        )
    if job.status is not JobStatus.EMAIL_GENERATED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_EMAIL_NOT_EDITABLE_DETAIL.format(status=job.status),
        )

    job_analysis.record_generated_email(subject=request.subject, body=request.body)
    job_analysis_repository.save(job_analysis)

    return _to_detail(job, job_analysis, cv_catalog)


@router.post("/{job_id}/generate-email", response_model=JobDetailResponse)
def generate_email(
    job_id: str,
    job_repository: JobRepository = Depends(get_job_repository),
    job_analysis_repository: JobAnalysisRepository = Depends(get_job_analysis_repository),
    llm_provider: LLMProvider = Depends(get_llm_provider),
    cv_catalog: CVCatalog = Depends(get_cv_catalog),
) -> JobDetailResponse:
    job = _get_job_or_404(job_repository, _parse_job_id(job_id))

    use_case = GenerateApplicationEmail(
        job_repository=job_repository,
        job_analysis_repository=job_analysis_repository,
        cv_catalog=cv_catalog,
        llm_provider=llm_provider,
    )
    try:
        use_case.execute_one(job)
    except InvalidStateTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except (LLMProviderError, LLMResponseValidationError) as exc:
        logger.warning(
            "jobs.generate_email.llm_call_failed job_id=%s error_type=%s",
            job.id,
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="The Email Generator LLM call failed. Check server logs for details.",
        ) from exc
    except CVInfrastructureError as exc:
        logger.error(
            "jobs.generate_email.cv_summary_failed job_id=%s error_type=%s",
            job.id,
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to resolve the recommended CV summary.",
        ) from exc

    job_analysis = job_analysis_repository.get_by_job_id(job.id)
    return _to_detail(job, job_analysis, cv_catalog)


def _to_application_response(application: Application) -> ApplicationResponse:
    return ApplicationResponse(
        id=str(application.id),
        job_id=str(application.job_id),
        email=str(application.email),
        subject=application.subject,
        body=application.body,
        cv_path=application.cv_path,
        gmail_draft_id=application.gmail_draft_id,
        gmail_url=build_gmail_url(application.gmail_draft_id),
        status=application.status,
        sent_at=application.sent_at,
    )


_DRAFT_RACE_LOST_DETAIL = (
    "A concurrent request already created the Gmail draft for this job, but its "
    "Application row is not visible yet. Retry the request."
)


@router.post("/{job_id}/create-draft", response_model=ApplicationResponse)
def create_draft(
    job_id: str,
    job_repository: JobRepository = Depends(get_job_repository),
    job_analysis_repository: JobAnalysisRepository = Depends(get_job_analysis_repository),
    application_repository: ApplicationRepository = Depends(get_application_repository),
    cv_catalog: CVCatalog = Depends(get_cv_catalog),
    email_draft_repository: EmailDraftRepository = Depends(get_email_draft_repository),
    session: Session = Depends(get_db_session),
) -> ApplicationResponse:
    """Creates (or reuses) a Gmail draft for a `Job` in `EMAIL_GENERATED`.

    Ver el docstring del módulo para el criterio completo de traducción de
    excepciones a HTTP (sección "Fase 7 (segunda ronda)"). El propio use
    case (`CreateGmailDraft.execute_one`) es idempotente -- un reintento tras
    un timeout de red hacia Gmail nunca crea un segundo draft, ver su
    docstring -- así que este handler no necesita ninguna guarda adicional
    de idempotencia por su cuenta **salvo** por la carrera de concurrencia
    real que sí maneja acá (ver docstring del módulo, entrada de
    `IntegrityError`): dos requests genuinamente simultáneas para el mismo
    `Job` pueden ambas pasar el chequeo de idempotencia del use case antes de
    que cualquiera comitee, así que el propio use case no puede evitar el
    segundo `create_draft` de Gmail ni el segundo `save()` por su cuenta --
    necesita la `Session` cruda del request (`get_db_session`) para
    recuperarse con un `rollback()` explícito, algo que el use case no recibe
    (ADR-004, solo repositorios ya construidos sobre la `Session`).
    """
    job = _get_job_or_404(job_repository, _parse_job_id(job_id))

    use_case = CreateGmailDraft(
        job_repository=job_repository,
        job_analysis_repository=job_analysis_repository,
        application_repository=application_repository,
        cv_catalog=cv_catalog,
        email_draft_repository=email_draft_repository,
    )
    try:
        application = use_case.execute_one(job)
    except InvalidStateTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except InconsistentJobPipelineStateError as exc:
        logger.error(
            "jobs.create_draft.inconsistent_pipeline_state job_id=%s error_type=%s",
            job.id,
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The job pipeline is in an inconsistent state. Check server logs for details.",
        ) from exc
    except CVInfrastructureError as exc:
        logger.error(
            "jobs.create_draft.cv_catalog_failed job_id=%s error_type=%s",
            job.id,
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to resolve the recommended CV path.",
        ) from exc
    except GmailInfrastructureError as exc:
        logger.warning(
            "jobs.create_draft.gmail_call_failed job_id=%s error_type=%s",
            job.id,
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="The Gmail draft creation call failed. Check server logs for details.",
        ) from exc
    except IntegrityError as exc:
        # Carrera de concurrencia real (ver docstring del módulo): otra
        # request ganó la carrera y ya insertó la Application para este
        # job_id entre el chequeo de idempotencia del use case y este
        # flush(). La Session queda inutilizable hasta el rollback -- no
        # alcanza con capturar la excepción y volver a consultar sin él.
        #
        # Este `except` envuelve todo `use_case.execute_one(job)`, no
        # específicamente el `flush()` de `application_repository.save()` --
        # asume que, dado el esquema actual, el único origen posible de
        # `IntegrityError` en este flujo es ese `save()` (el `job_repository
        # .save()` del paso 8 de `execute_one` solo hace un `UPDATE` de
        # status sobre una fila ya existente, sin ningún constraint que
        # pueda violar). Si `execute_one` cambiara de orden o se agregara un
        # constraint alcanzable desde el paso del `Job`, un `IntegrityError`
        # no relacionado con esta carrera podría dispararse *después* de que
        # `application_repository.save()` ya tuvo éxito -- revisar esta
        # asunción si ese escenario deja de ser cierto.
        session.rollback()
        logger.warning(
            "jobs.create_draft.application_race job_id=%s error_type=%s",
            job.id,
            type(exc).__name__,
        )
        winning_application = application_repository.get_by_job_id(job.id)
        if winning_application is not None:
            return _to_application_response(winning_application)
        # Debería ser prácticamente inalcanzable contra PostgreSQL real: un
        # segundo INSERT con la misma clave que un UniqueConstraint bloquea
        # hasta que la transacción ganadora termina, y solo levanta
        # IntegrityError si esa transacción efectivamente comiteó -- para
        # cuando este flush() se desbloquea con el error, la fila ganadora
        # ya debería estar comiteada y visible acá. Se deja como fallback
        # defensivo igual (barato, evita una excepción no manejada si esa
        # asunción alguna vez deja de sostenerse, p. ej. bajo otro nivel de
        # aislamiento) -- sin reintentar en loop (YAGNI, MVP local de un
        # solo usuario): se le pide al cliente reintentar.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=_DRAFT_RACE_LOST_DETAIL
        ) from exc

    return _to_application_response(application)
