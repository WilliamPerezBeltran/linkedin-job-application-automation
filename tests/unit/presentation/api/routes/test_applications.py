"""Unit tests for `/api/applications/**` (`app/presentation/api/routes/applications.py`).

Mismo patrón que `tests/unit/presentation/api/routes/test_jobs.py`
(`TestClient` de FastAPI + `app.dependency_overrides` con repos fake), nunca
Postgres real.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.domain.entities.application import Application
from app.domain.entities.job import Job
from app.domain.value_objects.application_id import ApplicationId
from app.domain.value_objects.application_status import ApplicationStatus
from app.domain.value_objects.email_address import EmailAddress
from app.domain.value_objects.job_id import JobId
from app.domain.value_objects.job_status import JobStatus
from app.main import app
from app.presentation.api.dependencies import get_application_repository, get_job_repository

pytestmark = pytest.mark.unit

client = TestClient(app)

_NOW = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Fakes -- mismo patrón que test_jobs.py.
# ---------------------------------------------------------------------------


class FakeJobRepository:
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
        matching = sorted(
            (job for job in self._jobs_by_id.values() if job.status == status),
            key=lambda job: job.created_at,
        )
        return matching[offset : offset + limit]

    def seed(self, job: Job) -> None:
        self._jobs_by_id[job.id] = job


class FakeApplicationRepository:
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

    def list_by_status(
        self, status: ApplicationStatus, *, limit: int = 50, offset: int = 0
    ) -> list[Application]:
        matching = [app_ for app_ in self._by_id.values() if app_.status == status]
        return matching[offset : offset + limit]

    def seed(self, application: Application) -> None:
        self._by_id[application.id] = application


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_job(
    *,
    content_hash: str = "a" * 64,
    status: JobStatus = JobStatus.DRAFT_CREATED,
) -> Job:
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
    job.mark_relevant()
    job.mark_cv_selected()
    job.mark_email_generated()
    job.mark_draft_created()
    if status is JobStatus.DRAFT_CREATED:
        return job
    job.mark_sent()
    return job


def _make_application(
    *, job_id: JobId, status: ApplicationStatus = ApplicationStatus.DRAFT_CREATED
) -> Application:
    application = Application.create(
        job_id=job_id,
        email=EmailAddress("recruiter@example.com"),
        subject="Application for Java role",
        body="Dear team...",
        cv_path="cvs/java/william-java.pdf",
        gmail_draft_id="draft-123",
    )
    if status is ApplicationStatus.SENT:
        application.mark_sent(sent_at=_NOW)
    return application


@pytest.fixture(autouse=True)
def _clear_dependency_overrides() -> Iterator[None]:
    yield
    app.dependency_overrides.clear()


def _override_repositories(
    application_repository: FakeApplicationRepository, job_repository: FakeJobRepository
) -> None:
    app.dependency_overrides[get_application_repository] = lambda: application_repository
    app.dependency_overrides[get_job_repository] = lambda: job_repository


# ---------------------------------------------------------------------------
# POST /api/applications/{id}/mark-sent
# ---------------------------------------------------------------------------


class TestMarkSent:
    def test_returns_200_and_marks_both_application_and_job_sent(self) -> None:
        job_repository = FakeJobRepository()
        application_repository = FakeApplicationRepository()
        job = _make_job(content_hash="b" * 64, status=JobStatus.DRAFT_CREATED)
        job_repository.seed(job)
        application = _make_application(job_id=job.id)
        application_repository.seed(application)
        _override_repositories(application_repository, job_repository)

        response = client.post(f"/api/applications/{application.id}/mark-sent")

        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "SENT"
        assert payload["sent_at"] is not None

        stored_application = application_repository.get_by_id(application.id)
        assert stored_application is not None
        assert stored_application.status == ApplicationStatus.SENT
        assert stored_application.sent_at is not None

        stored_job = job_repository.get_by_id(job.id)
        assert stored_job is not None
        assert stored_job.status == JobStatus.SENT

    def test_returns_404_when_application_does_not_exist(self) -> None:
        job_repository = FakeJobRepository()
        application_repository = FakeApplicationRepository()
        _override_repositories(application_repository, job_repository)

        response = client.post(f"/api/applications/{ApplicationId.new()}/mark-sent")

        assert response.status_code == 404

    def test_returns_404_for_a_malformed_id(self) -> None:
        job_repository = FakeJobRepository()
        application_repository = FakeApplicationRepository()
        _override_repositories(application_repository, job_repository)

        response = client.post("/api/applications/not-a-uuid/mark-sent")

        assert response.status_code == 404

    def test_returns_409_when_application_already_sent(self) -> None:
        job_repository = FakeJobRepository()
        application_repository = FakeApplicationRepository()
        job = _make_job(content_hash="c" * 64, status=JobStatus.SENT)
        job_repository.seed(job)
        application = _make_application(job_id=job.id, status=ApplicationStatus.SENT)
        application_repository.seed(application)
        _override_repositories(application_repository, job_repository)

        response = client.post(f"/api/applications/{application.id}/mark-sent")

        assert response.status_code == 409
        stored_application = application_repository.get_by_id(application.id)
        assert stored_application is not None
        assert stored_application.status == ApplicationStatus.SENT

    def test_returns_500_when_associated_job_is_missing(self) -> None:
        # Estado corrupto que no debería ocurrir en un pipeline sano (ver
        # docstring de `mark_sent`) -- forzado acá igual que
        # `test_jobs.py::TestCreateDraft.test_returns_500_when_pipeline_state_is_inconsistent`
        # fuerza su propio caso borde de estado inconsistente.
        job_repository = FakeJobRepository()
        application_repository = FakeApplicationRepository()
        application = _make_application(job_id=JobId.new())
        application_repository.seed(application)
        _override_repositories(application_repository, job_repository)

        response = client.post(f"/api/applications/{application.id}/mark-sent")

        assert response.status_code == 500
        stored_application = application_repository.get_by_id(application.id)
        assert stored_application is not None
        # The Application mutation already happened in memory (this fake
        # repository has no real transaction to roll back), but a real
        # Session would roll it back too via session_scope() -- see the
        # module docstring's "no abre transacciones manualmente" section.
        assert stored_application.status == ApplicationStatus.SENT
