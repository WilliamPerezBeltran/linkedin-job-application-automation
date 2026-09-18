"""Integration test for `SQLAlchemyApplicationRepository` against a real
PostgreSQL.

Mismo patrón que `test_sqlalchemy_job_analysis_repository.py`: salta
limpiamente con `pytest.skip` si no hay conexión disponible, y testea el
comportamiento prometido por la interfaz `ApplicationRepository` (structural
typing) — no SQLAlchemy directamente.

`applications.job_id` es FK a `jobs.id` (ver
`app/infrastructure/database/sqlalchemy_models.py`), así que cada test
primero persiste un `Job` (vía `SQLAlchemyJobRepository`) antes de guardar su
`Application` asociada.

`test_save_raises_integrity_error_on_second_application_for_same_job`
verifica específicamente el `UniqueConstraint("job_id", name=
"uq_applications_job_id")`: el flujo actual (`CreateGmailDraft`, capa de
aplicación) asume que un `Job` tiene a lo sumo una `Application` y usa
`get_by_job_id()` para idempotencia — sin este constraint a nivel de DB, dos
requests casi simultáneas podrían insertar dos filas para el mismo `job_id`
antes de que la primera hiciera commit.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.domain.entities.application import Application
from app.domain.entities.job import Job
from app.domain.value_objects.application_id import ApplicationId
from app.domain.value_objects.application_status import ApplicationStatus
from app.domain.value_objects.email_address import EmailAddress
from app.domain.value_objects.job_id import JobId
from app.infrastructure.database.repositories.sqlalchemy_application_repository import (
    SQLAlchemyApplicationRepository,
)
from app.infrastructure.database.repositories.sqlalchemy_job_repository import (
    SQLAlchemyJobRepository,
)
from app.infrastructure.database.session import get_engine, get_session_factory

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)


def _skip_reason_if_database_unavailable() -> str | None:
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
    except OperationalError as exc:
        return f"PostgreSQL no disponible ({exc.__class__.__name__}): {exc}"
    return None


@pytest.fixture
def db_session() -> Iterator[Session]:
    reason = _skip_reason_if_database_unavailable()
    if reason is not None:
        pytest.skip(reason)

    session = get_session_factory()()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _make_job(**overrides: object) -> Job:
    defaults: dict[str, object] = {
        "source": "linkedin_feed",
        "author": "Jane Recruiter",
        "content": "We are hiring a Senior Python Engineer...",
        "content_hash": f"itest-app-{JobId.new()}",
        "email": EmailAddress("jobs@example.com"),
        "url": "https://www.linkedin.com/feed/update/urn:li:activity:123/",
        "published_at": _NOW,
        "scraped_at": _NOW,
        "created_at": _NOW,
    }
    defaults.update(overrides)
    return Job.create(**defaults)  # type: ignore[arg-type]


def _make_application(job_id: JobId, **overrides: object) -> Application:
    defaults: dict[str, object] = {
        "job_id": job_id,
        "email": EmailAddress("jobs@example.com"),
        "subject": "Application for Senior Python Engineer",
        "body": "Dear Hiring Manager, ...",
        "cv_path": "cvs/python/william-python.pdf",
        "gmail_draft_id": "draft-123",
    }
    defaults.update(overrides)
    return Application.create(**defaults)  # type: ignore[arg-type]


def _delete_job(session: Session, job_id: JobId) -> None:
    """Cleans up a test job row directly (cascades to `applications` via
    `ondelete=CASCADE`), keeping the test repeatable across runs."""
    from app.infrastructure.database.sqlalchemy_models import JobModel

    model = session.get(JobModel, job_id.value)
    if model is not None:
        session.delete(model)
        session.commit()


class TestSQLAlchemyApplicationRepositoryIntegration:
    def test_save_then_get_by_id_returns_an_equivalent_application(
        self, db_session: Session
    ) -> None:
        job_repository = SQLAlchemyJobRepository(db_session)
        application_repository = SQLAlchemyApplicationRepository(db_session)
        job = _make_job()
        application = _make_application(job.id)

        try:
            job_repository.save(job)
            application_repository.save(application)
            db_session.commit()

            fetched = application_repository.get_by_id(application.id)

            assert fetched is not None
            assert fetched.id == application.id
            assert fetched.job_id == application.job_id
            assert str(fetched.email) == str(application.email)
            assert fetched.subject == application.subject
            assert fetched.body == application.body
            assert fetched.cv_path == application.cv_path
            assert fetched.gmail_draft_id == application.gmail_draft_id
            assert fetched.status == application.status
            assert fetched.sent_at == application.sent_at
        finally:
            _delete_job(db_session, job.id)

    def test_get_by_job_id_returns_the_application_for_that_job(
        self, db_session: Session
    ) -> None:
        job_repository = SQLAlchemyJobRepository(db_session)
        application_repository = SQLAlchemyApplicationRepository(db_session)
        job = _make_job()
        application = _make_application(job.id)

        try:
            job_repository.save(job)
            application_repository.save(application)
            db_session.commit()

            fetched = application_repository.get_by_job_id(job.id)

            assert fetched is not None
            assert fetched.id == application.id
        finally:
            _delete_job(db_session, job.id)

    def test_get_by_id_returns_none_when_missing(self, db_session: Session) -> None:
        application_repository = SQLAlchemyApplicationRepository(db_session)

        assert application_repository.get_by_id(ApplicationId.new()) is None

    def test_get_by_job_id_returns_none_when_missing(self, db_session: Session) -> None:
        application_repository = SQLAlchemyApplicationRepository(db_session)

        assert application_repository.get_by_job_id(JobId.new()) is None

    def test_save_raises_integrity_error_on_second_application_for_same_job(
        self, db_session: Session
    ) -> None:
        """`uq_applications_job_id` (ver `sqlalchemy_models.py` y la migración
        `ff54eda02789`) impide dos filas `applications` para el mismo
        `job_id`, incluso si ambas se intentan insertar antes de que la
        primera haga `commit()` — el escenario de dos requests casi
        simultáneas de `POST /api/jobs/{id}/create-draft` que motivó este
        constraint."""
        job_repository = SQLAlchemyJobRepository(db_session)
        application_repository = SQLAlchemyApplicationRepository(db_session)
        job = _make_job()
        first_application = _make_application(job.id)
        second_application = _make_application(job.id, gmail_draft_id="draft-456")

        try:
            job_repository.save(job)
            application_repository.save(first_application)
            db_session.commit()

            with pytest.raises(IntegrityError):
                application_repository.save(second_application)
        finally:
            db_session.rollback()
            _delete_job(db_session, job.id)

    def test_list_by_status_returns_only_applications_in_that_status_ordered_by_sent_at(
        self, db_session: Session
    ) -> None:
        """`list_by_status` (Fase 8, dashboard de histórico) filtra por
        `status` y ordena `sent_at` ascendente -- ver el docstring del
        Protocol (`ApplicationRepository.list_by_status`) para el contrato
        completo, incluyendo por qué `sent_at` (no `created_at`) y el
        desempate por `id`."""
        job_repository = SQLAlchemyJobRepository(db_session)
        application_repository = SQLAlchemyApplicationRepository(db_session)

        older_sent_job = _make_job(content_hash=f"itest-app-older-{JobId.new()}")
        newer_sent_job = _make_job(content_hash=f"itest-app-newer-{JobId.new()}")
        draft_job = _make_job(content_hash=f"itest-app-draft-{JobId.new()}")

        newer_sent = _make_application(newer_sent_job.id, gmail_draft_id="draft-newer")
        newer_sent.mark_sent(sent_at=datetime(2026, 9, 15, 12, 0, tzinfo=UTC))
        older_sent = _make_application(older_sent_job.id, gmail_draft_id="draft-older")
        older_sent.mark_sent(sent_at=datetime(2026, 9, 10, 12, 0, tzinfo=UTC))
        draft = _make_application(draft_job.id, gmail_draft_id="draft-pending")

        jobs = [older_sent_job, newer_sent_job, draft_job]
        try:
            for job in jobs:
                job_repository.save(job)
            for application in (newer_sent, older_sent, draft):
                application_repository.save(application)
            db_session.commit()

            fetched = application_repository.list_by_status(ApplicationStatus.SENT)

            fetched_ids = [application.id for application in fetched]
            assert older_sent.id in fetched_ids
            assert newer_sent.id in fetched_ids
            assert draft.id not in fetched_ids
            assert fetched_ids.index(older_sent.id) < fetched_ids.index(newer_sent.id)
            assert all(application.status == ApplicationStatus.SENT for application in fetched)
        finally:
            for job in jobs:
                _delete_job(db_session, job.id)

    def test_list_by_status_respects_limit_and_offset(self, db_session: Session) -> None:
        """Mismo criterio que
        `test_sqlalchemy_job_repository.py::test_list_by_status_respects_limit_and_offset`:
        no asume que la tabla está vacía de `Application` en `SENT` --
        ubica dónde caen las propias filas dentro del orden global y
        verifica paginación relativa a esa posición."""
        job_repository = SQLAlchemyJobRepository(db_session)
        application_repository = SQLAlchemyApplicationRepository(db_session)

        jobs = [
            _make_job(content_hash=f"itest-app-page-{index}-{JobId.new()}") for index in range(3)
        ]
        applications = [
            _make_application(job.id, gmail_draft_id=f"draft-page-{index}")
            for index, job in enumerate(jobs)
        ]
        for index, application in enumerate(applications):
            application.mark_sent(sent_at=datetime(2026, 9, 13, index, 0, tzinfo=UTC))

        try:
            for job in jobs:
                job_repository.save(job)
            for application in applications:
                application_repository.save(application)
            db_session.commit()

            full_ordering = application_repository.list_by_status(
                ApplicationStatus.SENT, limit=1_000_000, offset=0
            )
            full_ids = [application.id for application in full_ordering]
            start = full_ids.index(applications[0].id)

            first_page = application_repository.list_by_status(
                ApplicationStatus.SENT, limit=2, offset=start
            )
            second_page = application_repository.list_by_status(
                ApplicationStatus.SENT, limit=2, offset=start + 2
            )

            assert [application.id for application in first_page] == full_ids[start : start + 2]
            assert [application.id for application in second_page] == full_ids[
                start + 2 : start + 4
            ]
            assert {application.id for application in first_page}.isdisjoint(
                {application.id for application in second_page}
            )

            our_ids_in_order = [
                application_id
                for application_id in full_ids[start : start + 4]
                if application_id in {application.id for application in applications}
            ]
            assert our_ids_in_order == [applications[0].id, applications[1].id, applications[2].id]
        finally:
            for job in jobs:
                _delete_job(db_session, job.id)

    def test_list_by_status_returns_empty_list_when_no_match(self, db_session: Session) -> None:
        job_repository = SQLAlchemyJobRepository(db_session)
        application_repository = SQLAlchemyApplicationRepository(db_session)
        job = _make_job()
        application = _make_application(job.id)

        try:
            job_repository.save(job)
            application_repository.save(application)
            db_session.commit()

            assert application_repository.list_by_status(ApplicationStatus.SENT) == []
        finally:
            _delete_job(db_session, job.id)
