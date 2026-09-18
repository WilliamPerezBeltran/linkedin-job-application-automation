"""Persistence contract for the `Application` entity.

`Protocol` (no ABC): la implementación concreta
(`SQLAlchemyApplicationRepository`, Fase 7, `database-agent`) vive en
`app/infrastructure/database/repositories/` y satisface esta interfaz por
structural typing, sin heredar de nada definido aquí — el dominio no conoce
SQLAlchemy ni PostgreSQL.

Un `Protocol` propio, mismo criterio ya usado por `JobRepository` y
`JobAnalysisRepository`: `Application` es una entidad de dominio standalone
con ciclo de vida propio (`DRAFT_CREATED -> SENT`), escrita en fases
distintas por casos de uso distintos (Fase 7 `CreateGmailDraft`, Fase 8
`mark_sent`).

No hace falta un método separado de "update": el flujo siempre es cargar
con `get_by_id`, mutar la entidad ya cargada (p. ej. `mark_sent`) y volver a
pasar por `save` — mismo criterio de upsert que `JobRepository.save` y
`JobAnalysisRepository.save`.
"""

from __future__ import annotations

from typing import Protocol

from app.domain.entities.application import Application
from app.domain.value_objects.application_id import ApplicationId
from app.domain.value_objects.application_status import ApplicationStatus
from app.domain.value_objects.job_id import JobId


class ApplicationRepository(Protocol):
    def save(self, application: Application) -> None:
        """Persists an `Application` (insert or update, keyed by `application.id`).

        Misma semántica de upsert que `JobRepository.save`: la primera
        llamada (Fase 7, `CreateGmailDraft`) inserta; la llamada siguiente
        (Fase 8, tras `mark_sent`) actualiza la misma fila sobre la
        instancia ya cargada con `get_by_id`.
        """
        ...

    def get_by_id(self, application_id: ApplicationId) -> Application | None:
        """Returns the `Application` with the given id, or `None` if it doesn't exist.

        La rehidratación desde estado persistido debe hacerse siempre vía
        `Application.reconstruct()`, nunca `Application.create()` — `create`
        está reservado para dar de alta una `Application` nueva (Fase 7,
        siempre en `DRAFT_CREATED`), mientras que una fila leída de la base
        puede estar en cualquier estado válido (`DRAFT_CREATED` o `SENT`).
        """
        ...

    def get_by_job_id(self, job_id: JobId) -> Application | None:
        """Returns the `Application` for the given `job_id`, or `None`.

        Se agrega ahora (no es prematuro/YAGNI) porque `CreateGmailDraft`
        (Fase 7, `backend-engineer`) lo necesita desde el primer día: antes
        de crear un draft de Gmail para un `Job`, debe comprobar si ya
        existe una `Application` asociada, para no duplicar drafts si el
        endpoint se reintenta (p. ej. tras un timeout de red hacia Gmail).
        Un `Job` tiene a lo sumo una `Application` en el flujo actual (no
        hay reintento de postulación tras `SENT`), por eso este método
        devuelve un único `Application | None` y no una lista.
        """
        ...

    def list_by_status(
        self, status: ApplicationStatus, *, limit: int = 50, offset: int = 0
    ) -> list[Application]:
        """Returns Applications currently in `status`, paginated.

        Se agrega en Fase 8 para el dashboard de histórico/trazabilidad
        (ver ADR-004 sección 1, mismo criterio que
        `JobRepository.list_by_status`): necesita listar todas las
        `Application` en `SENT` para calcular "aplicaciones enviadas por
        semana" y "por categoría" (esto último cruzando cada `Application`
        con `JobAnalysis.job_type` vía `job_id`, fuera de este repositorio).

        Orden: `sent_at` ascendente (oldest-first), **no** `created_at` —
        `Application` no tiene ese campo hoy (ver columnas de `applications`
        en `CLAUDE.md`: `id, job_id, email, subject, body, cv_path,
        gmail_draft_id, status, sent_at`) y agregarlo solo para poder
        ordenar sería especulativo (YAGNI) dado que el único consumidor
        real de este método pagina `Application`s en estado `SENT`, y el
        invariante de la entidad (`Application.__init__`,
        `_require_status_sent_at_consistency`) garantiza `sent_at` no nulo
        para toda fila en `SENT` — alcanza para "enviadas por semana" sin
        campo adicional. Para `status=DRAFT_CREATED`, `sent_at` es siempre
        `None` por el mismo invariante, así que el orden entre esas filas
        queda sin garantizar (no hay hoy un caso de uso que pagine
        `DRAFT_CREATED` y dependa de un orden estable; si aparece, hay que
        reconsiderar añadir `created_at`). La columna/índice exacto es
        detalle de implementación de `database-agent`.

        Desempate: si dos `Application` en `SENT` comparten el mismo
        `sent_at` (colisión de timestamp, p. ej. por resolución de columna),
        la implementación debe desempatar por `id` para que la paginación
        sea estable entre llamadas — mismo espíritu que el requisito de
        `JobRepository.list_by_status` de "no repetir/saltar filas" entre
        requests.
        """
        ...
