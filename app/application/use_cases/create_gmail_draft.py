"""`CreateGmailDraft`: creates a Gmail draft (never sends) for a single `Job`
already in `EMAIL_GENERATED` (Fase 7, ROADMAP.md).

Para un `Job` ya cargado por quien llama (`execute_one`, mismo criterio de
extracción que `AnalyzeJobPost.execute_one`/`GenerateApplicationEmail.execute_one`
-- ver sus docstrings):

1. Idempotencia primero (`docs/agents/AGENTS.md` sección 22, diagrama "Same
   Application -> Existing Draft? -> Reuse/Create"): si
   `ApplicationRepository.get_by_job_id(job.id)` ya devuelve una
   `Application`, se la devuelve tal cual, sin llamar de nuevo a
   `EmailDraftRepository.create_draft` ni tocar `job.status`. Esto cubre el
   caso de que el endpoint (`POST /api/jobs/{id}/create-draft`, Fase 7,
   segunda ronda) se reintente tras un timeout de red hacia Gmail: nunca se
   crea un segundo draft para el mismo `Job`. Si ya existe una
   `Application`, `job.status` ya debería estar en `DRAFT_CREATED` (lo puso
   la corrida anterior que sí completó el paso 8) -- este método no lo
   revalida ni lo corrige, confía en esa invariante del propio pipeline.
2. Si no hay `Application` previa, valida que `job.status` sea
   `EMAIL_GENERATED` con `JobStatus.can_transition_to` (predicado público de
   solo lectura, sin aplicar la transición) **antes** de llamar a
   `EmailDraftRepository.create_draft` -- a diferencia del resto de los use
   cases del pipeline (`AnalyzeJobPost`/`GenerateApplicationEmail`), que
   documentan explícitamente no chequear `job.status` de antemano y dejar
   que el propio `mark_*` de la entidad lo valide al final. Acá se aparta
   deliberadamente de ese criterio general porque el efecto colateral entre
   medio no es una llamada a un LLM (barata, sin rastro visible fuera de la
   propia base de datos si falla) sino la creación de un draft real y
   persistente en la bandeja de Gmail del usuario: si `job.mark_draft_created()`
   fuera lo único que validara el estado, un `Job` con status corrupto (sin
   `Application` asociada, caso que no debería ocurrir pero que este método
   ya se defiende de en el resto de sus pasos) dispararía un draft real
   antes de que la validación de estado fallara -- violando la garantía de
   "nunca se crea un segundo draft"/"nunca un draft para un `Job` que no
   corresponde" que este mismo docstring promete. La excepción que lanza
   este chequeo temprano es la misma `InvalidStateTransitionError` que
   `Job.mark_draft_created()` lanzaría más tarde (mismo `entity_name`,
   `entity_id`, `current_status`, `target_status`) -- no una regla de
   negocio nueva, solo la misma decisión de la entidad, leída antes en vez
   de aplicada, para poder abortar sin efectos colaterales.
3. Busca la `JobAnalysis` asociada
   (`JobAnalysisRepository.get_by_job_id`) para obtener `subject`,
   `generated_email` (body) y `recommended_cv`. En un pipeline sano un `Job`
   en `EMAIL_GENERATED` siempre tiene una `JobAnalysis` con esos tres campos
   ya seteados -- lo garantiza `GenerateApplicationEmail.execute_one`, que
   exige `recommended_cv` (vía `JobAnalysis.record_generated_email`) antes
   de marcar `EMAIL_GENERATED`. Igual que `GenerateApplicationEmail` se
   defiende de una `JobAnalysis` faltante aunque no debería ocurrir, este
   método se defiende de que falte la `JobAnalysis` o de que le falten esos
   campos, lanzando `InconsistentJobPipelineStateError` -- a diferencia de
   `GenerateApplicationEmail.execute_one` (que cuenta ese caso como un
   `Outcome` más, `MISSING_ANALYSIS`, porque `execute()` procesa un batch y
   no puede abortarlo por un solo `Job` corrupto), acá no hay batch: un
   único `Job` con estado corrupto en un status que garantiza lo contrario
   es un bug del pipeline, no un dato transitorio esperable, y merece
   abortar ruidosamente en vez de degradarse en silencio.
4. Resuelve el destinatario (`to: EmailAddress`) desde `job.email` (el email
   de contacto extraído por el Analyzer en Fase 3, ver
   `Job.record_extracted_email`). Si `job.email` es `None`, es el mismo tipo
   de estado inconsistente que el punto 3 -- también
   `InconsistentJobPipelineStateError` (nada garantiza hoy que un `Job`
   llegue a `EMAIL_GENERATED` con `email` seteado; es una invariante
   deseable del pipeline, no una ya codificada en ninguna entidad de
   dominio, así que se defiende acá).
5. Resuelve `cv_path` a partir de `job_analysis.recommended_cv` con
   `CVCatalog.list_cvs()` (`app/application/cv/cv_catalog.py`, ownership
   `cv-matching-agent`), buscando el `CVProfile` cuyo `id` coincide -- mismo
   patrón que `app/presentation/api/routes/jobs.py::_resolve_skill_match`
   usa para resolver el `CVProfile` recomendado. `CVProfile.file` es la ruta
   **tal como está declarada en el catálogo** (relativa a la raíz del repo,
   p. ej. `cvs/java/william-java.pdf`), no una ruta absoluta ya resuelta en
   disco -- `CVProfile.file` (ver su docstring) deja explícito que resolverla
   contra el `base_dir` correcto es responsabilidad de quien la adjunte
   (`GmailDraftRepository`, Fase 7, `gmail-agent`), no de este use case ni de
   `EmailDraftRepository` como contrato. No existe en este `Protocol` un
   método "get por id" que devuelva un solo perfil, así que no hay forma más
   directa de resolverlo sin recorrer la lista completa. Si `list_cvs()`
   propaga `CVInfrastructureError` (ADR-006,
   `docs/decisions/006-llm-and-cv-exception-boundaries.md`), se deja
   propagar sin atrapar -- mismo criterio que
   `GenerateApplicationEmail.execute_one`. Si `list_cvs()` responde con
   éxito pero ningún perfil coincide con `recommended_cv` (el catálogo
   cambió entre la corrida del CV Matcher y esta), es otra instancia del
   mismo estado inconsistente de los puntos 3/4 -- también
   `InconsistentJobPipelineStateError`.
6. Llama `EmailDraftRepository.create_draft(to=..., subject=..., body=...,
   cv_path=...)` (`app/application/interfaces/email_draft_repository.py`).
   Puede propagar excepciones de infraestructura propias de `gmail-agent`
   (OAuth, API de Gmail, adjunto no encontrado) -- se dejan propagar sin
   atrapar, mismo criterio que el resto de los Protocols de infraestructura
   consumidos por este módulo. Es, deliberadamente, el último paso antes de
   escribir nada: todas las guardas de estado inconsistente (puntos 2-5) ya
   pasaron, así que el único motivo por el que este paso puede fallar es un
   problema real de infraestructura de Gmail, no un dato del pipeline.
7. Con el `gmail_draft_id` devuelto, se loguea de inmediato (`job_id` +
   `gmail_draft_id`, `logger.info`) **antes** de intentar persistir nada --
   el draft ya es real e irreversible en la bandeja de Gmail del usuario en
   este punto, así que si el siguiente paso (`ApplicationRepository.save`)
   falla (p. ej. `IntegrityError` por la carrera de concurrencia documentada
   en `app/presentation/api/routes/jobs.py::create_draft`, dos requests
   simultáneas para el mismo `Job`), este log es el único rastro operable
   que permite encontrar y, si hace falta, limpiar manualmente ese draft
   huérfano -- sin él, un `gmail_draft_id` perdido en esa carrera no queda
   registrado en ningún lado (`GmailDraftRepository.create_draft` solo
   loguea que un draft se creó, no su id). Luego crea la `Application`
   (`Application.create(...)`, siempre arranca en `DRAFT_CREATED`) y la
   persiste con `ApplicationRepository.save`.
8. Marca `job.mark_draft_created()` (`EMAIL_GENERATED -> DRAFT_CREATED`;
   la guarda del punto 2 ya garantiza que esta transición es válida acá, así
   que en la práctica nunca falla, pero se sigue llamando por la vía normal
   de la entidad -- nunca se asigna `job.status` a mano) y persiste el
   `Job`.
9. Devuelve la `Application` creada (o la existente, si el paso 1 ya la
   encontró).

No abre ni cierra transacción: recibe `JobRepository`, `JobAnalysisRepository`
y `ApplicationRepository` ya construidos sobre la misma `Session` activa (ver
`session_scope()` en `app/infrastructure/database/session.py`) -- mismo
criterio que `AnalyzeJobPost`/`GenerateApplicationEmail`. Un único
`session.commit()` al final, a cargo de quien construye este use case (el
composition root del endpoint `POST /api/jobs/{id}/create-draft`, todavía no
implementado -- segunda ronda de Fase 7, una vez que `gmail-agent` entregue
`GmailDraftRepository`, la implementación concreta de
`EmailDraftRepository`).

No expone `execute()` en batch como `AnalyzeJobPost`/`GenerateApplicationEmail`:
a diferencia de esos dos pasos, crear un draft de Gmail es una acción con
efecto colateral externo visible (un draft real aparece en la bandeja del
usuario) que ROADMAP/`CLAUDE.md` atan a una revisión manual explícita por
`Job` (dashboard de Fase 6) antes de dispararla -- no hay, hoy, un requisito
de "crear drafts para todos los `EMAIL_GENERATED` en lote". Si esa necesidad
aparece más adelante, agregar un `execute(limit=...)` que reuse
`execute_one()` en un loop es directo (mismo patrón que los use cases
anteriores) -- no se adelanta ahora por YAGNI.
"""

from __future__ import annotations

import logging

from app.application.cv.cv_catalog import CVCatalog
from app.application.interfaces.email_draft_repository import EmailDraftRepository
from app.domain.entities.application import Application
from app.domain.entities.job import Job
from app.domain.exceptions.invalid_state_transition_error import InvalidStateTransitionError
from app.domain.repositories.application_repository import ApplicationRepository
from app.domain.repositories.job_analysis_repository import JobAnalysisRepository
from app.domain.repositories.job_repository import JobRepository
from app.domain.value_objects.job_id import JobId
from app.domain.value_objects.job_status import JobStatus

logger = logging.getLogger(__name__)


class InconsistentJobPipelineStateError(Exception):
    """A `Job` in `EMAIL_GENERATED` is missing data the pipeline guarantees
    for that status.

    Nunca debería ocurrir en un pipeline sano: todo `Job` en
    `EMAIL_GENERATED` fue marcado así por
    `GenerateApplicationEmail.execute_one`, que exige `JobAnalysis.subject`/
    `generated_email`/`recommended_cv` ya seteados antes de esa transición.
    Se modela como una excepción de aplicación explícita (no una `Exception`
    genérica) para que quien la capture (el futuro endpoint
    `POST /api/jobs/{id}/create-draft`) pueda distinguirla de fallos de
    infraestructura (`CVInfrastructureError`, excepciones de `gmail-agent`) y
    traducirla a una respuesta HTTP que refleje "bug del pipeline", no "dato
    del usuario inválido" ni "servicio externo caído".

    `reason` es texto libre pensado para logs/debugging (nunca contenido
    sensible del post/email), no una clave estructurada -- no hay hoy más de
    un caller que necesite distinguir programáticamente entre las distintas
    causas (`JobAnalysis` faltante, campos faltantes en `JobAnalysis`, email
    faltante, `recommended_cv` ya no presente en el catálogo); si eso cambia,
    dividir en subclases concretas es una extensión directa.
    """

    def __init__(self, *, job_id: JobId, reason: str) -> None:
        super().__init__(f"Job {job_id} has an inconsistent pipeline state: {reason}")
        self.job_id = job_id
        self.reason = reason


class CreateGmailDraft:
    """Use case: create a Gmail draft (never send) for a single `Job` in
    `EMAIL_GENERATED`, and record it as an `Application`."""

    def __init__(
        self,
        job_repository: JobRepository,
        job_analysis_repository: JobAnalysisRepository,
        application_repository: ApplicationRepository,
        cv_catalog: CVCatalog,
        email_draft_repository: EmailDraftRepository,
    ) -> None:
        self._job_repository = job_repository
        self._job_analysis_repository = job_analysis_repository
        self._application_repository = application_repository
        self._cv_catalog = cv_catalog
        self._email_draft_repository = email_draft_repository

    def execute_one(self, job: Job) -> Application:
        """Creates (or reuses) the `Application`/Gmail draft for a single,
        already-loaded `job`.

        Ver el docstring del módulo para el criterio completo, en particular
        el punto 2: a diferencia de `AnalyzeJobPost.execute_one`/
        `GenerateApplicationEmail.execute_one`, este método sí valida
        `job.status` de antemano (antes de tocar Gmail), en vez de dejar que
        `Job.mark_draft_created()` sea la única guarda -- para no crear un
        draft real de Gmail antes de confirmar que la transición de estado
        es válida.
        """
        existing_application = self._application_repository.get_by_job_id(job.id)
        if existing_application is not None:
            return existing_application

        if not job.status.can_transition_to(JobStatus.DRAFT_CREATED):
            raise InvalidStateTransitionError(
                entity_name="Job",
                entity_id=str(job.id),
                current_status=job.status.value,
                target_status=JobStatus.DRAFT_CREATED.value,
            )

        job_analysis = self._job_analysis_repository.get_by_job_id(job.id)
        if job_analysis is None:
            raise InconsistentJobPipelineStateError(
                job_id=job.id, reason="no JobAnalysis found for a Job in EMAIL_GENERATED"
            )
        if (
            job_analysis.generated_email is None
            or job_analysis.subject is None
            or job_analysis.recommended_cv is None
        ):
            raise InconsistentJobPipelineStateError(
                job_id=job.id,
                reason=(
                    "JobAnalysis is missing subject/generated_email/recommended_cv "
                    "for a Job in EMAIL_GENERATED"
                ),
            )

        to = job.email
        if to is None:
            raise InconsistentJobPipelineStateError(
                job_id=job.id, reason="Job has no email for a Job in EMAIL_GENERATED"
            )

        cv_path = self._resolve_cv_path(job_id=job.id, cv_id=job_analysis.recommended_cv)

        gmail_draft_id = self._email_draft_repository.create_draft(
            to=to,
            subject=job_analysis.subject,
            body=job_analysis.generated_email,
            cv_path=cv_path,
        )
        # Logueado antes de cualquier intento de persistencia -- ver
        # docstring del módulo, punto 7: si `ApplicationRepository.save`
        # falla más abajo (p. ej. la carrera de concurrencia que
        # `POST /api/jobs/{id}/create-draft` maneja), este log es el único
        # rastro de un draft de Gmail real que ya no se puede deshacer.
        logger.info(
            "create_gmail_draft.gmail_draft_created job_id=%s gmail_draft_id=%s",
            job.id,
            gmail_draft_id,
        )

        application = Application.create(
            job_id=job.id,
            email=to,
            subject=job_analysis.subject,
            body=job_analysis.generated_email,
            cv_path=cv_path,
            gmail_draft_id=gmail_draft_id,
        )
        self._application_repository.save(application)

        job.mark_draft_created()
        self._job_repository.save(job)

        return application

    def _resolve_cv_path(self, *, job_id: JobId, cv_id: str) -> str:
        """Resolves the catalog-declared path (`CVProfile.file`) of the CV
        catalog entry `cv_id` -- **not** an absolute path already resolved
        on disk (ver docstring del módulo, punto 5, y de
        `EmailDraftRepository.create_draft`).

        Propaga `CVInfrastructureError` (`app.application.cv.cv_catalog_errors`,
        ver ADR-006) sin atraparla si `CVCatalog.list_cvs()` falla. Si
        responde con éxito pero ningún perfil coincide con `cv_id`, es un
        estado inconsistente del pipeline (ver docstring del módulo, punto
        5) -- `InconsistentJobPipelineStateError`, no `CVInfrastructureError`,
        porque el catálogo en sí no falló.
        """
        cv_profile = next(
            (profile for profile in self._cv_catalog.list_cvs() if profile.id == cv_id), None
        )
        if cv_profile is None:
            raise InconsistentJobPipelineStateError(
                job_id=job_id,
                reason=f"recommended_cv '{cv_id}' no longer exists in the CV catalog",
            )
        return cv_profile.file
