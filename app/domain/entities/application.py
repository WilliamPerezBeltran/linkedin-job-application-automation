"""`Application` entity: a Gmail draft created for a `Job`, pending manual
review and send.

Campos según el modelo de datos de `CLAUDE.md` (tabla `applications`): `id,
job_id, email, subject, body, cv_path, gmail_draft_id, status, sent_at`.
"""

from __future__ import annotations

from datetime import datetime

from app.domain.exceptions.invalid_domain_value_error import InvalidDomainValueError
from app.domain.exceptions.invalid_state_transition_error import InvalidStateTransitionError
from app.domain.value_objects.application_id import ApplicationId
from app.domain.value_objects.application_status import ApplicationStatus
from app.domain.value_objects.email_address import EmailAddress
from app.domain.value_objects.job_id import JobId


class Application:
    """A job application: the email + CV attachment created as a Gmail draft.

    Igual que `Job`, el `status` es de solo lectura desde fuera; la única
    transición de negocio es la confirmación manual de envío (`mark_sent`,
    Fase 8 — "Mark as Sent"). Nunca se envía automáticamente
    (`CLAUDE.md`: "El envío final (Send) queda bajo aprobación humana").

    `cv_path` se valida contra segmentos `..` como invariante mínima de
    higiene (evitar que una ruta con traversal llegue hasta infraestructura
    de filesystem/adjuntos de Gmail); la selección real del CV es
    responsabilidad de `cv-matching-agent`, que siempre produce rutas dentro
    del catálogo conocido.
    """

    def __init__(
        self,
        *,
        id: ApplicationId,
        job_id: JobId,
        email: EmailAddress,
        subject: str,
        body: str,
        cv_path: str,
        gmail_draft_id: str | None,
        status: ApplicationStatus,
        sent_at: datetime | None,
    ) -> None:
        _require_non_empty("subject", subject)
        _require_non_empty("body", body)
        _require_valid_cv_path(cv_path)
        _require_status_sent_at_consistency(status=status, sent_at=sent_at)

        self._id = id
        self._job_id = job_id
        self._email = email
        self._subject = subject
        self._body = body
        self._cv_path = cv_path
        self._gmail_draft_id = gmail_draft_id
        self._status = status
        self._sent_at = sent_at

    @classmethod
    def create(
        cls,
        *,
        job_id: JobId,
        email: EmailAddress,
        subject: str,
        body: str,
        cv_path: str,
        gmail_draft_id: str | None,
    ) -> Application:
        """Creates a new `Application`, always starting in `DRAFT_CREATED`.

        Refleja que un `Application` solo existe una vez que el draft de
        Gmail ya fue creado (Fase 7) — no hay un estado "pendiente de
        draft" en esta entidad.
        """
        return cls(
            id=ApplicationId.new(),
            job_id=job_id,
            email=email,
            subject=subject,
            body=body,
            cv_path=cv_path,
            gmail_draft_id=gmail_draft_id,
            status=ApplicationStatus.DRAFT_CREATED,
            sent_at=None,
        )

    @classmethod
    def reconstruct(
        cls,
        *,
        id: ApplicationId,
        job_id: JobId,
        email: EmailAddress,
        subject: str,
        body: str,
        cv_path: str,
        gmail_draft_id: str | None,
        status: ApplicationStatus,
        sent_at: datetime | None,
    ) -> Application:
        """Rehydrates an `Application` from persisted state (repository use only)."""
        return cls(
            id=id,
            job_id=job_id,
            email=email,
            subject=subject,
            body=body,
            cv_path=cv_path,
            gmail_draft_id=gmail_draft_id,
            status=status,
            sent_at=sent_at,
        )

    @property
    def id(self) -> ApplicationId:
        return self._id

    @property
    def job_id(self) -> JobId:
        return self._job_id

    @property
    def email(self) -> EmailAddress:
        return self._email

    @property
    def subject(self) -> str:
        return self._subject

    @property
    def body(self) -> str:
        return self._body

    @property
    def cv_path(self) -> str:
        return self._cv_path

    @property
    def gmail_draft_id(self) -> str | None:
        return self._gmail_draft_id

    @property
    def status(self) -> ApplicationStatus:
        return self._status

    @property
    def sent_at(self) -> datetime | None:
        return self._sent_at

    def mark_sent(self, *, sent_at: datetime) -> None:
        """DRAFT_CREATED -> SENT: confirmación manual del usuario.

        `sent_at` lo provee quien llama (application layer), en vez de que
        la entidad consulte el reloj del sistema internamente — mantiene el
        dominio determinista y fácil de testear.
        """
        if not self._status.can_transition_to(ApplicationStatus.SENT):
            raise InvalidStateTransitionError(
                entity_name="Application",
                entity_id=str(self._id),
                current_status=self._status.value,
                target_status=ApplicationStatus.SENT.value,
            )
        self._status = ApplicationStatus.SENT
        self._sent_at = sent_at

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Application):
            return NotImplemented
        return self._id == other._id

    def __hash__(self) -> int:
        return hash(self._id)


def _require_non_empty(field_name: str, value: str) -> None:
    if not value or not value.strip():
        raise InvalidDomainValueError(field_name=field_name, reason="must not be empty")


def _require_valid_cv_path(cv_path: str) -> None:
    _require_non_empty("cv_path", cv_path)
    if ".." in cv_path.split("/"):
        raise InvalidDomainValueError(
            field_name="cv_path", reason="must not contain '..' path segments"
        )


def _require_status_sent_at_consistency(
    *, status: ApplicationStatus, sent_at: datetime | None
) -> None:
    if status is ApplicationStatus.SENT and sent_at is None:
        raise InvalidDomainValueError(
            field_name="sent_at", reason="must be set when status is SENT"
        )
    if status is ApplicationStatus.DRAFT_CREATED and sent_at is not None:
        raise InvalidDomainValueError(
            field_name="sent_at", reason="must be None while status is DRAFT_CREATED"
        )
