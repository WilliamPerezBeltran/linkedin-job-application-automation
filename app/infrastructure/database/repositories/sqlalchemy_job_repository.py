"""`SQLAlchemyJobRepository`: concrete PostgreSQL implementation of the
`JobRepository` Protocol (`app/domain/repositories/job_repository.py`).

Satisface el Protocol por *structural typing* (no hereda de nada definido en
domain — el dominio no conoce SQLAlchemy). Traduce en ambas direcciones
entre `Job` (entidad de dominio) y `JobModel` (fila de la tabla `jobs`):

* Dominio -> fila: `save()` lee los value objects/propiedades de `Job`
  (incluyendo `job.status`, ya validado por la propia entidad) y los
  escribe como columnas planas.
* Fila -> dominio: `get_by_id`/`get_by_content_hash` usan siempre
  `Job.reconstruct(...)`, nunca `Job.create(...)` — `create()` fuerza
  `status=SCRAPED` y saltarse eso al rehidratar una fila ya persistida
  reescribiría silenciosamente su estado real.

Límite transaccional (ver `docs/agents/AGENTS.md` sección 13 y
`app/infrastructure/database/session.py`): este repositorio no abre ni
cierra su propia transacción — recibe una `Session` ya creada por el
llamador (un use case, un test) y hace `flush()` para que las violaciones
de constraint (p. ej. `content_hash` duplicado) se manifiesten de inmediato,
pero el `commit()`/`rollback()` final es responsabilidad de quien posee la
unidad de trabajo (ver `session_scope()`). Esto es deliberado: así un use
case puede hacer `save()` sobre dos entidades relacionadas dentro de la
misma transacción, y nunca mantiene una transacción abierta mientras espera
una llamada a un LLM, a Playwright o a la API de Gmail (esas llamadas
externas ya deben haber terminado y devuelto su resultado antes de que el
use case invoke a este repositorio).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.entities.job import Job
from app.domain.value_objects.email_address import EmailAddress
from app.domain.value_objects.job_id import JobId
from app.domain.value_objects.job_status import JobStatus
from app.infrastructure.database.sqlalchemy_models import JobModel


class SQLAlchemyJobRepository:
    """PostgreSQL-backed `JobRepository` (see the Protocol for the contract)."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def save(self, job: Job) -> None:
        """Inserts or updates `job`, keyed by `job.id` (idempotent upsert).

        No hace commit — ver docstring del módulo. `flush()` sí se llama
        para que un `content_hash` duplicado levante `IntegrityError` en el
        momento de `save()`, no de forma diferida y confusa en un `commit()`
        posterior desconectado de esta llamada.
        """
        model = self._session.get(JobModel, job.id.value)
        if model is None:
            model = JobModel(id=job.id.value)
            self._session.add(model)

        model.source = job.source
        model.author = job.author
        model.content = job.content
        model.content_hash = job.content_hash
        model.email = str(job.email) if job.email is not None else None
        model.url = job.url
        model.published_at = job.published_at
        model.scraped_at = job.scraped_at
        model.status = job.status.value
        model.created_at = job.created_at

        self._session.flush()

    def get_by_id(self, job_id: JobId) -> Job | None:
        model = self._session.get(JobModel, job_id.value)
        return self._to_domain(model) if model is not None else None

    def get_by_content_hash(self, content_hash: str) -> Job | None:
        stmt = select(JobModel).where(JobModel.content_hash == content_hash)
        model = self._session.scalars(stmt).one_or_none()
        return self._to_domain(model) if model is not None else None

    def list_by_status(self, status: JobStatus, *, limit: int = 50, offset: int = 0) -> list[Job]:
        """Returns Jobs in `status`, oldest-first (`created_at` ascending),
        paginated by `limit`/`offset` — ver ADR-004 sección 1 para el
        contrato completo (un único método reutilizado por Analyzer, CV
        Matcher y dashboard)."""
        stmt = (
            select(JobModel)
            .where(JobModel.status == status.value)
            .order_by(JobModel.created_at.asc())
            .limit(limit)
            .offset(offset)
        )
        models = self._session.scalars(stmt).all()
        return [self._to_domain(model) for model in models]

    @staticmethod
    def _to_domain(model: JobModel) -> Job:
        return Job.reconstruct(
            id=JobId(model.id),
            source=model.source,
            author=model.author,
            content=model.content,
            content_hash=model.content_hash,
            email=EmailAddress(model.email) if model.email is not None else None,
            url=model.url,
            published_at=model.published_at,
            scraped_at=model.scraped_at,
            status=JobStatus(model.status),
            created_at=model.created_at,
        )
