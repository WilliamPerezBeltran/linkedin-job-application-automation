"""`POST /api/applications/{application_id}/mark-sent`: Fase 8 (`ROADMAP.md`)
-- confirmación manual de que el usuario ya envió el email desde Gmail.

`CLAUDE.md`/`ROADMAP.md` Fase 8 documentan esto como el flujo "simple" del
MVP (opuesto a la "opción avanzada, fase posterior" de detectar el envío
inspeccionando la API de Gmail): el usuario crea el draft (Fase 7), lo revisa
y lo envía a mano desde la interfaz real de Gmail, y luego pulsa un botón
"Mark as Sent" en el dashboard que solo actualiza el estado local -- este
endpoint **nunca** llama a la API de Gmail ni dispara ningún envío real, solo
registra que ya ocurrió.

La orquestación real (mutar `Application` + su `Job` asociado, ver el
docstring de `MarkApplicationSent` para el razonamiento completo de por qué
esto necesita un use case dedicado en vez de vivir inline en este router,
igual que `ignore_job`) vive en
`app/application/use_cases/mark_application_sent.py`. Este handler solo
resuelve el `{application_id}` de la URL, carga la `Application`, invoca el
use case, y traduce sus excepciones a HTTP -- mismo patrón que
`analyze_job`/`generate_email`/`create_draft` en `jobs.py`.

Traducción de excepciones a HTTP (mismo criterio que
`app/presentation/api/routes/jobs.py`, ver su docstring de módulo):

- `InvalidIdentifierError` (`{application_id}` no es un UUID válido) y
  "`Application` no existe" -> `404 Not Found`, mismo criterio que
  `_get_job_or_404`/`_parse_job_id`: desde la perspectiva del cliente, ambos
  casos significan "ese recurso no existe".
- `InvalidStateTransitionError` sobre `Application.mark_sent` (la
  `Application` no está en `DRAFT_CREATED`, p. ej. ya fue marcada `SENT`
  antes) -> `409 Conflict`, mismo criterio que `ignore_job`/`analyze_job`.
- `InconsistentApplicationPipelineStateError`
  (`app.application.use_cases.mark_application_sent`, el `Job` asociado no
  existe o no puede transicionar a `SENT` -- nunca debería ocurrir en un
  pipeline sano) -> `500 Internal Server Error`, mismo criterio que
  `InconsistentJobPipelineStateError` en `jobs.py::create_draft`.

No abre transacciones manualmente: `get_application_repository`/
`get_job_repository` comparten la misma `Session` de request (ver
`app/presentation/api/dependencies.py`), con un único commit al final del
request vía `session_scope()` -- ambos `save()` que hace el use case
(`Application` y `Job`) se confirman juntos o se revierten juntos si algo
falla antes de que el handler termine.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status

from app.application.dto.application_response import ApplicationResponse, build_gmail_url
from app.application.use_cases.mark_application_sent import (
    InconsistentApplicationPipelineStateError,
    MarkApplicationSent,
)
from app.domain.entities.application import Application
from app.domain.exceptions.invalid_identifier_error import InvalidIdentifierError
from app.domain.exceptions.invalid_state_transition_error import InvalidStateTransitionError
from app.domain.repositories.application_repository import ApplicationRepository
from app.domain.repositories.job_repository import JobRepository
from app.domain.value_objects.application_id import ApplicationId
from app.presentation.api.dependencies import get_application_repository, get_job_repository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/applications", tags=["applications"])

_APPLICATION_NOT_FOUND_DETAIL = "Application not found."
_PIPELINE_INCONSISTENT_DETAIL = (
    "The job pipeline is in an inconsistent state. Check server logs for details."
)


def _parse_application_id(raw_application_id: str) -> ApplicationId:
    """Parses a path `{application_id}` into an `ApplicationId`, mapping a
    malformed UUID to 404 -- mismo criterio que `_parse_job_id` en `jobs.py`.
    """
    try:
        return ApplicationId.of(raw_application_id)
    except InvalidIdentifierError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=_APPLICATION_NOT_FOUND_DETAIL
        ) from exc


def _get_application_or_404(
    application_repository: ApplicationRepository, application_id: ApplicationId
) -> Application:
    application = application_repository.get_by_id(application_id)
    if application is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=_APPLICATION_NOT_FOUND_DETAIL
        )
    return application


def _to_application_response(application: Application) -> ApplicationResponse:
    """Mismo mapeo que `jobs.py::_to_application_response` (Fase 7) -- no se
    reusa esa función directamente (vive en otro router, ownership del mismo
    agente pero módulo distinto) para no crear un acoplamiento entre
    routers; ambas comparten el mismo DTO (`ApplicationResponse`).
    """
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


@router.post("/{application_id}/mark-sent", response_model=ApplicationResponse)
def mark_sent(
    application_id: str,
    application_repository: ApplicationRepository = Depends(get_application_repository),
    job_repository: JobRepository = Depends(get_job_repository),
) -> ApplicationResponse:
    """Confirms manually that `application_id` was sent from Gmail.

    `sent_at` se genera acá (`datetime.now(UTC)`), en el composition root del
    endpoint -- nunca dentro de la entidad `Application.mark_sent`, que lo
    recibe por parámetro para mantenerse determinista y testeable (ver su
    docstring en `app/domain/entities/application.py`).

    Delega toda la orquestación (mutar `Application` + su `Job` asociado, y
    el razonamiento de consistencia de datos detrás de eso) a
    `MarkApplicationSent.execute_one` -- ver el docstring de ese use case.
    """
    application = _get_application_or_404(
        application_repository, _parse_application_id(application_id)
    )

    use_case = MarkApplicationSent(
        application_repository=application_repository, job_repository=job_repository
    )
    try:
        application = use_case.execute_one(application, sent_at=datetime.now(UTC))
    except InvalidStateTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except InconsistentApplicationPipelineStateError as exc:
        logger.error(
            "applications.mark_sent.inconsistent_pipeline_state application_id=%s "
            "job_id=%s error_type=%s",
            exc.application_id,
            exc.job_id,
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_PIPELINE_INCONSISTENT_DETAIL,
        ) from exc

    return _to_application_response(application)
