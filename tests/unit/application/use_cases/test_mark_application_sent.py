"""Unit tests for `MarkApplicationSent`.

Usa repositorios en memoria -- mismo patrón que
`test_create_gmail_draft.py` -- para nunca depender de Gmail/Postgres real.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.application.use_cases.mark_application_sent import (
    InconsistentApplicationPipelineStateError,
    MarkApplicationSent,
)
from app.domain.entities.application import Application
from app.domain.entities.job import Job
from app.domain.exceptions.invalid_state_transition_error import InvalidStateTransitionError
from app.domain.value_objects.application_id import ApplicationId
from app.domain.value_objects.email_address import EmailAddress
from app.domain.value_objects.job_id import JobId
from app.domain.value_objects.job_status import JobStatus

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)


class InMemoryApplicationRepository:
    def __init__(self) -> None:
        self._by_id: dict[ApplicationId, Application] = {}

    def save(self, application: Application) -> None:
        self._by_id[application.id] = application

    def get_by_id(self, application_id: ApplicationId) -> Application | None:
        return self._by_id.get(application_id)

    def get_by_job_id(self, job_id: JobId) -> Application | None:
        for application in self._by_id.values():
            if application.job_id == job_id:
                return application
        return None

    def seed(self, application: Application) -> None:
        self._by_id[application.id] = application


class InMemoryJobRepository:
    def __init__(self) -> None:
        self._jobs_by_id: dict[JobId, Job] = {}

    def save(self, job: Job) -> None:
        self._jobs_by_id[job.id] = job

    def get_by_id(self, job_id: JobId) -> Job | None:
        return self._jobs_by_id.get(job_id)

    def get_by_content_hash(self, content_hash: str) -> Job | None:
        for job in self._jobs_by_id.values():
            if job.content_hash == content_hash:
                return job
        return None

    def list_by_status(self, status: JobStatus, *, limit: int = 50, offset: int = 0) -> list[Job]:
        matching = [job for job in self._jobs_by_id.values() if job.status == status]
        return matching[offset : offset + limit]

    def seed(self, job: Job) -> None:
        self._jobs_by_id[job.id] = job


def _make_job(*, content_hash: str, status: JobStatus = JobStatus.DRAFT_CREATED) -> Job:
    job = Job.create(
        source="linkedin",
        author="Jane Recruiter",
        content="We are hiring a Senior Java Engineer, remote, apply now!",
        content_hash=content_hash,
        email=EmailAddress("recruiter@example.com"),
        url=f"https://www.linkedin.com/feed/update/{content_hash}/",
        published_at=_NOW,
        scraped_at=_NOW,
        created_at=_NOW,
    )
    if status is JobStatus.SCRAPED:
        return job
    job.mark_analyzed()
    if status is JobStatus.ANALYZED:
        return job
    job.mark_relevant()
    if status is JobStatus.RELEVANT:
        return job
    job.mark_cv_selected()
    if status is JobStatus.CV_SELECTED:
        return job
    job.mark_email_generated()
    if status is JobStatus.EMAIL_GENERATED:
        return job
    job.mark_draft_created()
    return job


def _make_application(*, job_id: JobId) -> Application:
    application = Application.create(
        job_id=job_id,
        email=EmailAddress("recruiter@example.com"),
        subject="Application for Java role",
        body="Dear team...",
        cv_path="cvs/java/william-java.pdf",
        gmail_draft_id="draft-123",
    )
    return application


class TestMarkApplicationSentHappyPath:
    def test_marks_both_application_and_job_sent(self) -> None:
        job = _make_job(content_hash="a" * 64, status=JobStatus.DRAFT_CREATED)
        job_repository = InMemoryJobRepository()
        job_repository.seed(job)
        application = _make_application(job_id=job.id)
        application_repository = InMemoryApplicationRepository()
        application_repository.seed(application)

        use_case = MarkApplicationSent(
            application_repository=application_repository, job_repository=job_repository
        )
        sent_at = datetime(2026, 9, 18, 8, 0, tzinfo=UTC)
        result = use_case.execute_one(application, sent_at=sent_at)

        assert result.status.value == "SENT"
        assert result.sent_at == sent_at

        stored_application = application_repository.get_by_id(application.id)
        assert stored_application is not None
        assert stored_application.status.value == "SENT"

        stored_job = job_repository.get_by_id(job.id)
        assert stored_job is not None
        assert stored_job.status == JobStatus.SENT


class TestMarkApplicationSentPropagatesApplicationTransitionErrors:
    def test_raises_when_application_already_sent(self) -> None:
        job = _make_job(content_hash="b" * 64, status=JobStatus.DRAFT_CREATED)
        job.mark_sent()
        job_repository = InMemoryJobRepository()
        job_repository.seed(job)
        application = _make_application(job_id=job.id)
        application.mark_sent(sent_at=_NOW)
        application_repository = InMemoryApplicationRepository()
        application_repository.seed(application)

        use_case = MarkApplicationSent(
            application_repository=application_repository, job_repository=job_repository
        )

        with pytest.raises(InvalidStateTransitionError):
            use_case.execute_one(application, sent_at=datetime(2026, 9, 19, tzinfo=UTC))


class TestMarkApplicationSentHandlesInconsistentPipelineState:
    def test_raises_when_associated_job_does_not_exist(self) -> None:
        # Estado corrupto que no debería ocurrir en un pipeline sano (ver
        # docstring del use case): una Application sin su Job asociado.
        application = _make_application(job_id=JobId.new())
        application_repository = InMemoryApplicationRepository()
        application_repository.seed(application)
        job_repository = InMemoryJobRepository()

        use_case = MarkApplicationSent(
            application_repository=application_repository, job_repository=job_repository
        )

        with pytest.raises(InconsistentApplicationPipelineStateError):
            use_case.execute_one(application, sent_at=_NOW)

        # The Application mutation already happened in memory -- a real
        # Session would roll it back too via session_scope() (see the
        # use case's module docstring).
        stored_application = application_repository.get_by_id(application.id)
        assert stored_application is not None
        assert stored_application.status.value == "SENT"

    def test_raises_when_job_cannot_transition_to_sent(self) -> None:
        # Otra instancia del mismo invariante roto: el Job asociado existe
        # pero se quedó atrás en un status anterior a DRAFT_CREATED (nunca
        # debería pasar si CreateGmailDraft corrió correctamente).
        job = _make_job(content_hash="c" * 64, status=JobStatus.EMAIL_GENERATED)
        job_repository = InMemoryJobRepository()
        job_repository.seed(job)
        application = _make_application(job_id=job.id)
        application_repository = InMemoryApplicationRepository()
        application_repository.seed(application)

        use_case = MarkApplicationSent(
            application_repository=application_repository, job_repository=job_repository
        )

        with pytest.raises(InconsistentApplicationPipelineStateError):
            use_case.execute_one(application, sent_at=_NOW)

        assert job.status == JobStatus.EMAIL_GENERATED
        stored_application = application_repository.get_by_id(application.id)
        assert stored_application is not None
        assert stored_application.status.value == "SENT"
