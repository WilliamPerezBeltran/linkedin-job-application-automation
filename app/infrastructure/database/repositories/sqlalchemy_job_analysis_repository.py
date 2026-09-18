"""`SQLAlchemyJobAnalysisRepository`: concrete PostgreSQL implementation of
the `JobAnalysisRepository` Protocol
(`app/domain/repositories/job_analysis_repository.py`).

Satisface el Protocol por *structural typing* (no hereda de nada definido en
domain — el dominio no conoce SQLAlchemy). Traduce en ambas direcciones
entre `JobAnalysis` (entidad de dominio) y `JobAnalysisModel` (fila de la
tabla `job_analysis`, PK/FK `job_id`, ver
`app/infrastructure/database/sqlalchemy_models.py`):

* Dominio -> fila: `save()` lee las propiedades de `JobAnalysis` y las
  escribe como columnas planas (upsert por `job_id`).
* Fila -> dominio: `get_by_job_id` reconstruye la entidad pasando todos los
  campos directamente al `__init__` de `JobAnalysis` — a diferencia de
  `Job`, `JobAnalysis` no tiene un concepto de `status` propio ni un método
  `reconstruct` separado de su constructor normal (no hay ninguna
  transición de estado que un constructor "de creación" pudiera pisar por
  error), así que no hace falta distinguir "crear nuevo" de "rehidratar
  desde persistencia" como sí ocurre en `SQLAlchemyJobRepository`.

Límite transaccional (ver `docs/agents/AGENTS.md` sección 13 y
`app/infrastructure/database/session.py`): igual que
`SQLAlchemyJobRepository`, este repositorio no abre ni cierra su propia
transacción — recibe una `Session` ya creada por el llamador y hace
`flush()`, no `commit()`. El `commit()`/`rollback()` final es
responsabilidad de quien posee la unidad de trabajo (`session_scope()`).

Nota de consistencia transaccional (ADR-004 sección 2,
`docs/decisions/004-job-pipeline-repositories.md`): `AnalyzeJobPost`
(Fase 3) necesita persistir un `JobAnalysis` nuevo (este repositorio) y
mover `Job.status` (`SQLAlchemyJobRepository`) en la misma operación
lógica. Para que eso sea atómico con el driver síncrono ya decidido
(ADR-002, "una `Session` por request/uso"), el use case debe:

1. Abrir una única `Session` (vía `session_scope()`).
2. Construir ambos repositorios concretos (`SQLAlchemyJobRepository`,
   `SQLAlchemyJobAnalysisRepository`) pasándoles **esa misma** `Session`.
3. Llamar a `save()` en ambos dentro de esa sesión — cada `save()` hace
   `flush()` mas no `commit()`.
4. Dejar que `session_scope()` (o el punto que abrió la unidad de trabajo)
   haga el único `commit()` al final, cubriendo ambas escrituras en la
   misma transacción de base de datos.

Este módulo, deliberadamente, no introduce ningún `UnitOfWork` explícito —
ADR-004 lo descarta por YAGNI (mismo criterio de ADR-001 nota de diseño 2).
Si en el futuro crece el número de repositorios que deben comitear juntos,
o aparecen bugs de consistencia parcial, evaluar un ADR nuevo.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.domain.entities.job_analysis import JobAnalysis
from app.domain.value_objects.job_id import JobId
from app.infrastructure.database.sqlalchemy_models import JobAnalysisModel


class SQLAlchemyJobAnalysisRepository:
    """PostgreSQL-backed `JobAnalysisRepository` (ver el Protocol para el
    contrato)."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def save(self, job_analysis: JobAnalysis) -> None:
        """Inserts or updates `job_analysis`, keyed by `job_id` (idempotent
        upsert).

        No hace commit — ver docstring del módulo. `flush()` sí se llama
        para que una violación de constraint (p. ej. `job_id` sin fila
        correspondiente en `jobs`, FK) levante `IntegrityError` en el
        momento de `save()`, no de forma diferida en un `commit()`
        posterior desconectado de esta llamada.
        """
        model = self._session.get(JobAnalysisModel, job_analysis.job_id.value)
        if model is None:
            model = JobAnalysisModel(job_id=job_analysis.job_id.value)
            self._session.add(model)

        model.job_type = job_analysis.job_type
        model.seniority = job_analysis.seniority
        model.skills = list(job_analysis.skills)
        model.languages = list(job_analysis.languages)
        model.frameworks = list(job_analysis.frameworks)
        model.cloud = list(job_analysis.cloud)
        model.ai_related = job_analysis.ai_related
        model.match_score = job_analysis.match_score
        model.recommended_cv = job_analysis.recommended_cv
        model.generated_email = job_analysis.generated_email
        model.subject = job_analysis.subject

        self._session.flush()

    def get_by_job_id(self, job_id: JobId) -> JobAnalysis | None:
        model = self._session.get(JobAnalysisModel, job_id.value)
        return self._to_domain(model) if model is not None else None

    @staticmethod
    def _to_domain(model: JobAnalysisModel) -> JobAnalysis:
        return JobAnalysis(
            job_id=JobId(model.job_id),
            job_type=model.job_type,
            seniority=model.seniority,
            skills=model.skills,
            languages=model.languages,
            frameworks=model.frameworks,
            cloud=model.cloud,
            ai_related=model.ai_related,
            match_score=model.match_score,
            recommended_cv=model.recommended_cv,
            generated_email=model.generated_email,
            subject=model.subject,
        )
