"""Persistence contract for the `Job` entity.

`Protocol` (no ABC): la implementación concreta (`SQLAlchemyJobRepository`,
Fase 1, `database-agent`) vive en `app/infrastructure/database/repositories/`
y satisface esta interfaz por structural typing, sin heredar de nada
definido aquí — el dominio no conoce SQLAlchemy ni PostgreSQL.

Alcance deliberadamente mínimo para la Fase 1 (ver ROADMAP "Done cuando":
insertar un `Job` y leerlo de vuelta contra esta interfaz, con dedup por
`content_hash`). Métodos adicionales se agregan en fases posteriores cuando
un use case concreto los necesite (YAGNI) — `list_by_status` se agregó en
Fase 3 según ADR-004 sección 1 (`docs/decisions/004-job-pipeline-repositories.md`):
un único método reutilizado por el Analyzer (Fase 3), el CV Matcher (Fase 4)
y el dashboard (Fase 6), en vez de un método distinto por caso de uso.
"""

from __future__ import annotations

from typing import Protocol

from app.domain.entities.job import Job
from app.domain.value_objects.job_id import JobId
from app.domain.value_objects.job_status import JobStatus


class JobRepository(Protocol):
    def save(self, job: Job) -> None:
        """Persists a `Job` (insert or update, keyed by `job.id`)."""
        ...

    def get_by_id(self, job_id: JobId) -> Job | None:
        """Returns the `Job` with the given id, or `None` if it doesn't exist."""
        ...

    def get_by_content_hash(self, content_hash: str) -> Job | None:
        """Returns the `Job` with the given `content_hash`, or `None`.

        Usado por el collector (Fase 2) para deduplicar antes de insertar:
        LinkedIn puede repetir el mismo post varias veces en el feed.
        """
        ...

    def list_by_status(self, status: JobStatus, *, limit: int = 50, offset: int = 0) -> list[Job]:
        """Returns Jobs currently in `status`, oldest-first (created_at
        ascending), paginated.

        Un único método, reutilizado por los tres consumidores del pipeline
        (Analyzer Fase 3, CV Matcher Fase 4, dashboard Fase 6) en vez de un
        método distinto por caso de uso — ver ADR-004 sección 1. El orden
        (`created_at` ascendente, FIFO) es parte del contrato para que la
        paginación del dashboard no repita/salte filas entre requests si
        llegan jobs nuevos; la columna exacta de ordenamiento es detalle de
        implementación de `database-agent`. `limit`/`offset` tienen default
        porque el Analyzer/CV Matcher normalmente quieren "todo lo
        pendiente" mientras que el dashboard pagina explícitamente.
        """
        ...
