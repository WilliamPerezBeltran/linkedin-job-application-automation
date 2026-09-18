"""`SQLAlchemyApplicationRepository`: concrete PostgreSQL implementation of
the `ApplicationRepository` Protocol
(`app/domain/repositories/application_repository.py`).

Satisface el Protocol por *structural typing* (no hereda de nada definido en
domain — el dominio no conoce SQLAlchemy). Traduce en ambas direcciones
entre `Application` (entidad de dominio) y `ApplicationModel` (fila de la
tabla `applications`):

* Dominio -> fila: `save()` lee los value objects/propiedades de
  `Application` (incluyendo `application.status`, ya validado por la propia
  entidad) y los escribe como columnas planas.
* Fila -> dominio: `get_by_id`/`get_by_job_id` usan siempre
  `Application.reconstruct(...)`, nunca `Application.create(...)` —
  `create()` fuerza `status=DRAFT_CREATED` y `sent_at=None`, y saltarse eso
  al rehidratar una fila ya persistida reescribiría silenciosamente su
  estado real (p. ej. una `Application` ya marcada `SENT`).

Límite transaccional (mismo criterio que `SQLAlchemyJobRepository`, ver
`docs/agents/AGENTS.md` sección 13 y `app/infrastructure/database/
session.py`): este repositorio no abre ni cierra su propia transacción —
recibe una `Session` ya creada por el llamador (un use case, un test) y hace
`flush()` para que las violaciones de constraint se manifiesten de
inmediato, pero el `commit()`/`rollback()` final es responsabilidad de quien
posee la unidad de trabajo (ver `session_scope()`). Nunca se mantiene una
transacción abierta mientras se espera una llamada externa (LLM,
Playwright, Gmail API) — esas ya deben haber terminado antes de invocar a
este repositorio.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.entities.application import Application
from app.domain.value_objects.application_id import ApplicationId
from app.domain.value_objects.application_status import ApplicationStatus
from app.domain.value_objects.email_address import EmailAddress
from app.domain.value_objects.job_id import JobId
from app.infrastructure.database.sqlalchemy_models import ApplicationModel


class SQLAlchemyApplicationRepository:
    """PostgreSQL-backed `ApplicationRepository` (see the Protocol for the contract)."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def save(self, application: Application) -> None:
        """Inserts or updates `application`, keyed by `application.id` (idempotent upsert).

        No hace commit — ver docstring del módulo. `flush()` sí se llama
        para que una violación de constraint (p. ej. `job_id` inexistente)
        levante `IntegrityError` en el momento de `save()`, no de forma
        diferida y confusa en un `commit()` posterior desconectado de esta
        llamada.
        """
        model = self._session.get(ApplicationModel, application.id.value)
        if model is None:
            model = ApplicationModel(id=application.id.value)
            self._session.add(model)

        model.job_id = application.job_id.value
        model.email = str(application.email)
        model.subject = application.subject
        model.body = application.body
        model.cv_path = application.cv_path
        model.gmail_draft_id = application.gmail_draft_id
        model.status = application.status.value
        model.sent_at = application.sent_at

        self._session.flush()

    def get_by_id(self, application_id: ApplicationId) -> Application | None:
        model = self._session.get(ApplicationModel, application_id.value)
        return self._to_domain(model) if model is not None else None

    def get_by_job_id(self, job_id: JobId) -> Application | None:
        """Returns the `Application` for `job_id`, or `None` if none exists.

        Un `Job` tiene a lo sumo una `Application` en el flujo actual (ver
        docstring del Protocol de dominio) — `.one_or_none()` refleja esa
        invariante devolviendo `None` cuando no hay ninguna, y dejando que
        `MultipleResultsFound` se propague (en vez de silenciarla eligiendo
        la primera fila) si esa invariante llegara a romperse, ya que sería
        un bug de otra capa, no algo que este repositorio deba enmascarar.
        """
        stmt = select(ApplicationModel).where(ApplicationModel.job_id == job_id.value)
        model = self._session.scalars(stmt).one_or_none()
        return self._to_domain(model) if model is not None else None

    def list_by_status(
        self, status: ApplicationStatus, *, limit: int = 50, offset: int = 0
    ) -> list[Application]:
        """Returns Applications in `status`, oldest-first (`sent_at` ascending),
        paginated by `limit`/`offset` — ver el docstring del Protocol para el
        contrato completo (por qué `sent_at` y no `created_at`, y el
        desempate por `id`).

        Orden con dos columnas para paginación estable: `sent_at` es la
        clave principal, `id` desempata filas `SENT` con el mismo `sent_at`.
        Para `status=DRAFT_CREATED`, `sent_at` es siempre `NULL` (invariante
        de `Application`); el desempate por `id` sigue aplicando entre esas
        filas, así que la paginación no repite/salta filas aunque el orden
        relativo a `sent_at` no tenga significado — no hace falta NULLS
        FIRST/LAST explícito porque el Protocol no promete un orden
        particular para `DRAFT_CREATED`, solo estabilidad.
        """
        stmt = (
            select(ApplicationModel)
            .where(ApplicationModel.status == status.value)
            .order_by(ApplicationModel.sent_at.asc(), ApplicationModel.id.asc())
            .limit(limit)
            .offset(offset)
        )
        models = self._session.scalars(stmt).all()
        return [self._to_domain(model) for model in models]

    @staticmethod
    def _to_domain(model: ApplicationModel) -> Application:
        return Application.reconstruct(
            id=ApplicationId(model.id),
            job_id=JobId(model.job_id),
            email=EmailAddress(model.email),
            subject=model.subject,
            body=model.body,
            cv_path=model.cv_path,
            gmail_draft_id=model.gmail_draft_id,
            status=ApplicationStatus(model.status),
            sent_at=model.sent_at,
        )
