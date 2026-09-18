"""Persistence contract for the `JobAnalysis` entity.

`Protocol` (no ABC): la implementación concreta
(`SQLAlchemyJobAnalysisRepository`, Fase 3, `database-agent`) vive en
`app/infrastructure/database/repositories/` y satisface esta interfaz por
structural typing, sin heredar de nada definido aquí — el dominio no conoce
SQLAlchemy ni PostgreSQL.

Un `Protocol` propio por entidad con ciclo de vida propio, mismo criterio ya
usado por `JobRepository` (ver ADR-004 sección 2,
`docs/decisions/004-job-pipeline-repositories.md`): `JobAnalysis` es una
entidad de dominio standalone (no un value object embebido en `Job`), con
sus propios métodos de negocio (`record_cv_recommendation`,
`record_generated_email`) e invariantes propias (no se puede grabar
`generated_email` sin `recommended_cv`), escrita y reescrita en fases
distintas por agentes distintos (Analyzer Fase 3, CV Matcher Fase 4, Email
Generator Fase 5). Envolver su persistencia dentro de `JobRepository`
mezclaría en una misma interfaz dos entidades con ciclos de vida distintos,
violando Interface Segregation. No se agrega `list_by_*` aquí (YAGNI): todo
caller parte de `JobRepository.list_by_status(...)` y resuelve el análisis
asociado con `get_by_job_id` por cada `Job`.
"""

from __future__ import annotations

from typing import Protocol

from app.domain.entities.job_analysis import JobAnalysis
from app.domain.value_objects.job_id import JobId


class JobAnalysisRepository(Protocol):
    def save(self, job_analysis: JobAnalysis) -> None:
        """Persists a `JobAnalysis` (insert or update, keyed by `job_id`).

        Misma semántica de upsert que `JobRepository.save`: la primera
        llamada (Fase 3, Analyzer) inserta; las llamadas siguientes (Fase 4
        CV Matcher, Fase 5 Email Generator) actualizan la misma fila sobre
        la instancia ya cargada con `get_by_job_id`.
        """
        ...

    def get_by_job_id(self, job_id: JobId) -> JobAnalysis | None:
        """Returns the `JobAnalysis` for the given `job_id`, or `None`.

        `JobAnalysis` no tiene un identificador propio — se identifica por
        `job_id` (ver `__eq__`/`__hash__` de la entidad y la tabla
        `job_analysis`, que usa `job_id` como PK/FK) — por eso este método
        busca por `job_id`, no por un `JobAnalysisId` que no existe en el
        dominio.
        """
        ...
