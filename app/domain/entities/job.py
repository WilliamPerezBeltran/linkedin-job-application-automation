"""`Job` entity: a scraped LinkedIn feed post being tracked through the
application pipeline.

Campos según el modelo de datos de `CLAUDE.md` (tabla `jobs`): `id, source,
author, content, content_hash (unique), email, url, published_at,
scraped_at, status, created_at`.
"""

from __future__ import annotations

from datetime import datetime

from app.domain.exceptions.invalid_domain_value_error import InvalidDomainValueError
from app.domain.exceptions.invalid_state_transition_error import InvalidStateTransitionError
from app.domain.value_objects.email_address import EmailAddress
from app.domain.value_objects.job_id import JobId
from app.domain.value_objects.job_status import JobStatus


class Job:
    """A job post collected from the LinkedIn feed.

    El `status` es de solo lectura desde fuera de la entidad: solo se
    modifica a través de los métodos `mark_*`, que validan la transición
    contra `JobStatus.can_transition_to` antes de aplicarla. Esto es
    deliberado — el ROADMAP exige que "las transiciones válidas [estén]
    garantizadas por la propia entidad (no dejar que cualquier código
    externo asigne cualquier status)".

    Dos factorías cubren los dos orígenes legítimos de un `Job`:
    - `Job.create(...)`: alta de negocio, siempre arranca en `SCRAPED`.
    - `Job.reconstruct(...)`: rehidratación desde persistencia (usada por
      `JobRepository`), acepta el `status` ya almacenado tal cual, sin
      pasar por la máquina de transiciones (esos datos ya fueron válidos
      cuando se guardaron).
    """

    def __init__(
        self,
        *,
        id: JobId,
        source: str,
        author: str,
        content: str,
        content_hash: str,
        email: EmailAddress | None,
        url: str,
        published_at: datetime | None,
        scraped_at: datetime,
        status: JobStatus,
        created_at: datetime,
    ) -> None:
        _require_non_empty("source", source)
        _require_non_empty("author", author)
        _require_non_empty("content", content)
        _require_non_empty("content_hash", content_hash)
        _require_non_empty("url", url)

        self._id = id
        self._source = source
        self._author = author
        self._content = content
        self._content_hash = content_hash
        self._email = email
        self._url = url
        self._published_at = published_at
        self._scraped_at = scraped_at
        self._status = status
        self._created_at = created_at

    @classmethod
    def create(
        cls,
        *,
        source: str,
        author: str,
        content: str,
        content_hash: str,
        email: EmailAddress | None,
        url: str,
        published_at: datetime | None,
        scraped_at: datetime,
        created_at: datetime,
    ) -> Job:
        """Creates a brand-new `Job`, always starting in `SCRAPED`.

        `content_hash` se recibe ya calculado por quien scrapea (ver
        `app/infrastructure/linkedin/linkedin_feed_collector.py`, Fase 2) —
        el dominio valida que no esté vacío pero no decide el algoritmo de
        hashing, que es un detalle de infraestructura.
        """
        return cls(
            id=JobId.new(),
            source=source,
            author=author,
            content=content,
            content_hash=content_hash,
            email=email,
            url=url,
            published_at=published_at,
            scraped_at=scraped_at,
            status=JobStatus.SCRAPED,
            created_at=created_at,
        )

    @classmethod
    def reconstruct(
        cls,
        *,
        id: JobId,
        source: str,
        author: str,
        content: str,
        content_hash: str,
        email: EmailAddress | None,
        url: str,
        published_at: datetime | None,
        scraped_at: datetime,
        status: JobStatus,
        created_at: datetime,
    ) -> Job:
        """Rehydrates a `Job` from persisted state (repository use only)."""
        return cls(
            id=id,
            source=source,
            author=author,
            content=content,
            content_hash=content_hash,
            email=email,
            url=url,
            published_at=published_at,
            scraped_at=scraped_at,
            status=status,
            created_at=created_at,
        )

    @property
    def id(self) -> JobId:
        return self._id

    @property
    def source(self) -> str:
        return self._source

    @property
    def author(self) -> str:
        return self._author

    @property
    def content(self) -> str:
        return self._content

    @property
    def content_hash(self) -> str:
        return self._content_hash

    @property
    def email(self) -> EmailAddress | None:
        return self._email

    @property
    def url(self) -> str:
        return self._url

    @property
    def published_at(self) -> datetime | None:
        return self._published_at

    @property
    def scraped_at(self) -> datetime:
        return self._scraped_at

    @property
    def status(self) -> JobStatus:
        return self._status

    @property
    def created_at(self) -> datetime:
        return self._created_at

    def mark_analyzed(self) -> None:
        """SCRAPED -> ANALYZED: el Job Analyzer (LLM) terminó de procesarlo."""
        self._transition_to(JobStatus.ANALYZED)

    def mark_relevant(self) -> None:
        """ANALYZED -> RELEVANT: el analyzer determinó que es una oferta real."""
        self._transition_to(JobStatus.RELEVANT)

    def mark_not_relevant(self) -> None:
        """ANALYZED -> NOT_RELEVANT: estado terminal, no es una oferta relevante."""
        self._transition_to(JobStatus.NOT_RELEVANT)

    def mark_cv_selected(self) -> None:
        """RELEVANT -> CV_SELECTED: el CV Matcher ya eligió un CV."""
        self._transition_to(JobStatus.CV_SELECTED)

    def mark_email_generated(self) -> None:
        """CV_SELECTED -> EMAIL_GENERATED: subject/body ya generados."""
        self._transition_to(JobStatus.EMAIL_GENERATED)

    def mark_draft_created(self) -> None:
        """EMAIL_GENERATED -> DRAFT_CREATED: draft real creado en Gmail."""
        self._transition_to(JobStatus.DRAFT_CREATED)

    def mark_sent(self) -> None:
        """DRAFT_CREATED -> SENT: confirmación manual de envío."""
        self._transition_to(JobStatus.SENT)

    def mark_ignored(self) -> None:
        """{SCRAPED, ANALYZED, RELEVANT, CV_SELECTED, EMAIL_GENERATED} -> IGNORED.

        A diferencia del resto de los `mark_*`, que el pipeline automático
        dispara al completar un paso, esta es la única transición que
        representa una decisión manual del usuario tomada durante la
        revisión humana (dashboard, Fase 6) -- descartar la oferta antes de
        que exista un draft real de Gmail. Ver el docstring de `JobStatus`
        para el razonamiento completo de por qué `DRAFT_CREATED`/`SENT` no
        pueden transicionar aquí.
        """
        self._transition_to(JobStatus.IGNORED)

    def record_extracted_email(self, email: EmailAddress) -> None:
        """Fase 3 (Job Analyzer): registra el email de contacto detectado
        en el post (`DEBT-ADR005-01`, ver
        `docs/decisions/005-llm-provider-interface.md`).

        Sin transición de `status` asociada -- no representa un paso del
        pipeline, es un dato adicional ortogonal a la clasificación de
        relevancia, y por eso puede llamarse en cualquier status, antes o
        después de `mark_analyzed`/`mark_relevant`. Mismo patrón que
        `JobAnalysis.record_cv_recommendation`: mutador simple, sin pasar
        por `_transition_to`.

        Si ya había un email registrado (de `Job.create(...)` o de una
        llamada previa), esta llamada lo sobreescribe sin guarda: el email
        detectado por el Analyzer es, por diseño, la fuente más confiable y
        más reciente disponible para este `Job` -- no hay ninguna regla de
        negocio documentada que distinga "email original del post" de
        "email extraído por el LLM" como dos datos separados a conservar.
        """
        self._email = email

    def _transition_to(self, target: JobStatus) -> None:
        if not self._status.can_transition_to(target):
            raise InvalidStateTransitionError(
                entity_name="Job",
                entity_id=str(self._id),
                current_status=self._status.value,
                target_status=target.value,
            )
        self._status = target

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Job):
            return NotImplemented
        return self._id == other._id

    def __hash__(self) -> int:
        return hash(self._id)


def _require_non_empty(field_name: str, value: str) -> None:
    if not value or not value.strip():
        raise InvalidDomainValueError(field_name=field_name, reason="must not be empty")
