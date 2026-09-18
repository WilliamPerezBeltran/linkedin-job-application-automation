"""`MarkApplicationSent`: Fase 8 (`ROADMAP.md`, "Mark as Sent") -- confirms
manually that an `Application` was sent from Gmail, advancing both the
`Application` and its associated `Job` to `SENT` in the same unit of work.

Extraído a un use case dedicado (`app/application/use_cases/`), no inline en
`app/presentation/api/routes/applications.py` -- corrección de un hallazgo
MEDIUM de `code-reviewer` sobre la primera versión de esta fase: aunque la
operación *parece* tan simple como `ignore_job`
(`app/presentation/api/routes/jobs.py`, un único `mark_*` + `save` inline en
el router), en realidad coordina **dos** entidades (`Application` + `Job`)
sobre un invariante de negocio explícito -- "una `Application` en
`DRAFT_CREATED` implica que su `Job` asociado también lo está", garantizado
por `CreateGmailDraft.execute_one` (Fase 7, `app/application/use_cases/
create_gmail_draft.py`) -- con manejo defensivo de estado inconsistente del
pipeline si ese invariante se rompiera. Eso la hace estructuralmente igual a
`CreateGmailDraft`/`AnalyzeJobPost`/`GenerateApplicationEmail` (todas viven
en este paquete con sus propios tests unitarios), no a `ignore_job`, que solo
toca una entidad y no defiende ningún invariante entre entidades.

Flujo de `execute_one`:

1. `application.mark_sent(sent_at=sent_at)` -- puede lanzar
   `InvalidStateTransitionError` si `application.status` no era
   `DRAFT_CREATED` (p. ej. ya estaba `SENT`). Se deja propagar sin atrapar,
   igual que el resto de los use cases del pipeline dejan propagar los
   `InvalidStateTransitionError` de sus propias entidades -- es
   responsabilidad de quien llama (el endpoint HTTP) traducirla a `409`.
2. Persiste la `Application` ya mutada.
3. Carga el `Job` asociado (`application.job_id`). Si no existe, o si
   `job.mark_sent()` rechaza la transición (`DRAFT_CREATED -> SENT`), el
   invariante del punto 1 se rompió -- un bug del pipeline, no un dato de
   cliente inválido ni un estado alcanzable en un flujo sano -- y se lanza
   `InconsistentApplicationPipelineStateError`, mismo criterio que
   `InconsistentJobPipelineStateError` en `CreateGmailDraft`.
4. Persiste el `Job` ya mutado.

No abre ni cierra transacción: recibe `ApplicationRepository`/`JobRepository`
ya construidos sobre la misma `Session` activa (mismo criterio que el resto
de los use cases del pipeline) -- un único `session.commit()` al final, a
cargo del composition root del endpoint (`get_db_session` vía
`session_scope()`, ver `app/presentation/api/dependencies.py`). Si el paso 3
falla después de que el paso 2 ya persistió la `Application` (pero antes del
commit final del request), `session_scope()` revierte ambos cambios --
nunca queda una `Application` en `SENT` en base real con su `Job` sin
avanzar.
"""

from __future__ import annotations

from datetime import datetime

from app.domain.entities.application import Application
from app.domain.exceptions.invalid_state_transition_error import InvalidStateTransitionError
from app.domain.repositories.application_repository import ApplicationRepository
from app.domain.repositories.job_repository import JobRepository
from app.domain.value_objects.application_id import ApplicationId
from app.domain.value_objects.job_id import JobId


class InconsistentApplicationPipelineStateError(Exception):
    """The `Job` associated with an `Application` being marked `SENT` is
    missing, or cannot itself transition to `SENT`.

    Nunca debería ocurrir en un pipeline sano: `CreateGmailDraft.execute_one`
    (Fase 7) garantiza que toda `Application` en `DRAFT_CREATED` tiene un
    `Job` asociado también en `DRAFT_CREATED`. Se modela como una excepción
    de aplicación explícita (no una `Exception` genérica), mismo criterio
    que `InconsistentJobPipelineStateError`
    (`app/application/use_cases/create_gmail_draft.py`), para que quien la
    capture (el endpoint `POST /api/applications/{id}/mark-sent`) pueda
    distinguirla de un `InvalidStateTransitionError` de la propia
    `Application` y traducirla a `500` ("bug del pipeline") en vez de `409`
    ("dato del cliente inválido").

    Una clase propia, no reutiliza `InconsistentJobPipelineStateError`: esa
    excepción está definida en el módulo de `CreateGmailDraft` y describe un
    estado inconsistente distinto (un `Job` en `EMAIL_GENERATED` sin los
    campos que esa transición garantiza) -- acoplar este use case a esa
    excepción importaría un detalle interno de otro paso del pipeline sin
    necesidad real.
    """

    def __init__(self, *, application_id: ApplicationId, job_id: JobId, reason: str) -> None:
        super().__init__(
            f"Application {application_id} (job {job_id}) has an inconsistent "
            f"pipeline state: {reason}"
        )
        self.application_id = application_id
        self.job_id = job_id
        self.reason = reason


class MarkApplicationSent:
    """Use case: confirm manually that an `Application` was sent, advancing
    both it and its associated `Job` to `SENT`."""

    def __init__(
        self, application_repository: ApplicationRepository, job_repository: JobRepository
    ) -> None:
        self._application_repository = application_repository
        self._job_repository = job_repository

    def execute_one(self, application: Application, *, sent_at: datetime) -> Application:
        """Marks `application` (already loaded by the caller) as `SENT`,
        then marks its associated `Job` as `SENT` too.

        Ver el docstring del módulo para el criterio completo, en particular
        por qué `InvalidStateTransitionError` sobre la `Application` se deja
        propagar sin atrapar, mientras que cualquier problema con el `Job`
        asociado se traduce a `InconsistentApplicationPipelineStateError`.
        """
        application.mark_sent(sent_at=sent_at)
        self._application_repository.save(application)

        job = self._job_repository.get_by_id(application.job_id)
        if job is None:
            raise InconsistentApplicationPipelineStateError(
                application_id=application.id,
                job_id=application.job_id,
                reason="no Job found for this Application's job_id",
            )
        try:
            job.mark_sent()
        except InvalidStateTransitionError as exc:
            raise InconsistentApplicationPipelineStateError(
                application_id=application.id,
                job_id=application.job_id,
                reason=f"Job could not transition to SENT (current status: {job.status})",
            ) from exc
        self._job_repository.save(job)

        return application
