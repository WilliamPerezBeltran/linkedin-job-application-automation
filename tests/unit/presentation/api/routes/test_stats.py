"""Unit tests for `GET /api/stats/dashboard` (`app/presentation/api/routes/stats.py`).

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
from app.domain.entities.job_analysis import JobAnalysis
from app.domain.value_objects.application_id import ApplicationId
from app.domain.value_objects.application_status import ApplicationStatus
from app.domain.value_objects.email_address import EmailAddress
from app.domain.value_objects.job_id import JobId
from app.domain.value_objects.job_status import JobStatus
from app.main import app
from app.presentation.api.dependencies import (
    get_application_repository,
    get_job_analysis_repository,
    get_job_repository,
)
from app.presentation.api.routes.stats import _applications_sent_by_week

pytestmark = pytest.mark.unit

client = TestClient(app)


# ---------------------------------------------------------------------------
# Fakes -- mismo patrón que test_jobs.py/test_applications.py.
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
        matching = [job for job in self._jobs_by_id.values() if job.status == status]
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


class FakeJobAnalysisRepository:
    def __init__(self) -> None:
        self._by_job_id: dict[JobId, JobAnalysis] = {}

    def save(self, job_analysis: JobAnalysis) -> None:
        self._by_job_id[job_analysis.job_id] = job_analysis

    def get_by_job_id(self, job_id: JobId) -> JobAnalysis | None:
        return self._by_job_id.get(job_id)

    def seed(self, job_analysis: JobAnalysis) -> None:
        self._by_job_id[job_analysis.job_id] = job_analysis


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_job(*, content_hash: str, status: JobStatus) -> Job:
    job = Job.create(
        source="linkedin",
        author="Jane Recruiter",
        content="We are hiring a Senior Java Engineer, remote, apply now!",
        content_hash=content_hash,
        email=EmailAddress("recruiter@example.com"),
        url=f"https://www.linkedin.com/feed/update/{content_hash}/",
        published_at=datetime(2026, 1, 1, tzinfo=UTC),
        scraped_at=datetime(2026, 1, 1, tzinfo=UTC),
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    if status is JobStatus.SCRAPED:
        return job
    job.mark_analyzed()
    if status is JobStatus.ANALYZED:
        return job
    if status is JobStatus.NOT_RELEVANT:
        job.mark_not_relevant()
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
    if status is JobStatus.DRAFT_CREATED:
        return job
    job.mark_sent()
    return job


def _make_sent_application(
    *, job_id: JobId, sent_at: datetime, gmail_draft_id: str = "draft-123"
) -> Application:
    application = Application.create(
        job_id=job_id,
        email=EmailAddress("recruiter@example.com"),
        subject="Application for Java role",
        body="Dear team...",
        cv_path="cvs/java/william-java.pdf",
        gmail_draft_id=gmail_draft_id,
    )
    application.mark_sent(sent_at=sent_at)
    return application


def _make_job_analysis(*, job_id: JobId, job_type: str) -> JobAnalysis:
    return JobAnalysis(
        job_id=job_id,
        job_type=job_type,
        seniority="Senior",
        skills=("Java",),
        languages=("Java",),
        frameworks=(),
        cloud=(),
        ai_related=False,
    )


@pytest.fixture(autouse=True)
def _clear_dependency_overrides() -> Iterator[None]:
    yield
    app.dependency_overrides.clear()


def _override_repositories(
    job_repository: FakeJobRepository,
    application_repository: FakeApplicationRepository,
    job_analysis_repository: FakeJobAnalysisRepository,
) -> None:
    app.dependency_overrides[get_job_repository] = lambda: job_repository
    app.dependency_overrides[get_application_repository] = lambda: application_repository
    app.dependency_overrides[get_job_analysis_repository] = lambda: job_analysis_repository


# ---------------------------------------------------------------------------
# GET /api/stats/dashboard
# ---------------------------------------------------------------------------


class TestDashboardStats:
    def test_jobs_by_status_includes_all_nine_statuses_with_zero_counts(self) -> None:
        job_repository = FakeJobRepository()
        job_repository.seed(_make_job(content_hash="a" * 64, status=JobStatus.SCRAPED))
        job_repository.seed(_make_job(content_hash="b" * 64, status=JobStatus.SCRAPED))
        job_repository.seed(_make_job(content_hash="c" * 64, status=JobStatus.RELEVANT))
        _override_repositories(
            job_repository, FakeApplicationRepository(), FakeJobAnalysisRepository()
        )

        response = client.get("/api/stats/dashboard")

        assert response.status_code == 200
        jobs_by_status = response.json()["jobs_by_status"]
        assert set(jobs_by_status.keys()) == {status.value for status in JobStatus}
        assert jobs_by_status["SCRAPED"] == 2
        assert jobs_by_status["RELEVANT"] == 1
        assert jobs_by_status["SENT"] == 0
        assert jobs_by_status["IGNORED"] == 0

    def test_applications_sent_total_and_by_week(self) -> None:
        job_repository = FakeJobRepository()
        application_repository = FakeApplicationRepository()
        job_analysis_repository = FakeJobAnalysisRepository()

        job_week_38 = _make_job(content_hash="d" * 64, status=JobStatus.SENT)
        job_week_38_b = _make_job(content_hash="e" * 64, status=JobStatus.SENT)
        job_week_39 = _make_job(content_hash="f" * 64, status=JobStatus.SENT)
        job_repository.seed(job_week_38)
        job_repository.seed(job_week_38_b)
        job_repository.seed(job_week_39)

        # 2026-09-17 falls in ISO week 2026-W38; 2026-09-24 in 2026-W39.
        application_repository.seed(
            _make_sent_application(
                job_id=job_week_38.id, sent_at=datetime(2026, 9, 17, 12, tzinfo=UTC)
            )
        )
        application_repository.seed(
            _make_sent_application(
                job_id=job_week_38_b.id, sent_at=datetime(2026, 9, 18, 9, tzinfo=UTC)
            )
        )
        application_repository.seed(
            _make_sent_application(
                job_id=job_week_39.id, sent_at=datetime(2026, 9, 24, 9, tzinfo=UTC)
            )
        )
        _override_repositories(job_repository, application_repository, job_analysis_repository)

        response = client.get("/api/stats/dashboard")

        assert response.status_code == 200
        payload = response.json()
        assert payload["applications_sent_total"] == 3
        assert payload["applications_sent_by_week"] == [
            {"week": "2026-W38", "count": 2},
            {"week": "2026-W39", "count": 1},
        ]

    def test_applications_sent_by_category_uses_job_analysis_job_type(self) -> None:
        job_repository = FakeJobRepository()
        application_repository = FakeApplicationRepository()
        job_analysis_repository = FakeJobAnalysisRepository()

        java_job = _make_job(content_hash="1" * 64, status=JobStatus.SENT)
        python_job = _make_job(content_hash="2" * 64, status=JobStatus.SENT)
        job_repository.seed(java_job)
        job_repository.seed(python_job)
        job_analysis_repository.seed(_make_job_analysis(job_id=java_job.id, job_type="Java"))
        job_analysis_repository.seed(_make_job_analysis(job_id=python_job.id, job_type="Python"))
        application_repository.seed(
            _make_sent_application(job_id=java_job.id, sent_at=datetime(2026, 9, 17, tzinfo=UTC))
        )
        application_repository.seed(
            _make_sent_application(job_id=python_job.id, sent_at=datetime(2026, 9, 17, tzinfo=UTC))
        )
        _override_repositories(job_repository, application_repository, job_analysis_repository)

        response = client.get("/api/stats/dashboard")

        assert response.status_code == 200
        by_category = response.json()["applications_sent_by_category"]
        assert {"category": "Java", "count": 1} in by_category
        assert {"category": "Python", "count": 1} in by_category

    def test_missing_job_analysis_is_counted_as_unknown(self) -> None:
        # No debería ocurrir en un pipeline sano (ver docstring de
        # `_applications_sent_by_category`), pero se defiende igual --
        # mismo criterio que otros casos borde ya cubiertos en
        # `test_jobs.py` (`JobAnalysis` faltante).
        job_repository = FakeJobRepository()
        application_repository = FakeApplicationRepository()
        job_analysis_repository = FakeJobAnalysisRepository()

        orphan_job = _make_job(content_hash="3" * 64, status=JobStatus.SENT)
        job_repository.seed(orphan_job)
        application_repository.seed(
            _make_sent_application(job_id=orphan_job.id, sent_at=datetime(2026, 9, 17, tzinfo=UTC))
        )
        _override_repositories(job_repository, application_repository, job_analysis_repository)

        response = client.get("/api/stats/dashboard")

        assert response.status_code == 200
        by_category = response.json()["applications_sent_by_category"]
        assert {"category": "unknown", "count": 1} in by_category

    def test_no_sent_applications_returns_empty_aggregates(self) -> None:
        job_repository = FakeJobRepository()
        _override_repositories(
            job_repository, FakeApplicationRepository(), FakeJobAnalysisRepository()
        )

        response = client.get("/api/stats/dashboard")

        assert response.status_code == 200
        payload = response.json()
        assert payload["applications_sent_total"] == 0
        assert payload["applications_sent_by_week"] == []
        assert payload["applications_sent_by_category"] == []


class _ApplicationWithMissingSentAt:
    """Duck-typed stand-in for `Application` with `sent_at=None` despite
    being passed as a "sent" application -- `Application.__init__`'s own
    invariant (`_require_status_sent_at_consistency`) makes this state
    unreachable through a real domain `Application` in `SENT`, so this
    defensive branch (`_applications_sent_by_week`, see its docstring) can
    only be exercised by calling the private function directly with a
    double that doesn't go through that invariant -- same spirit as the
    "should never happen" defenses already tested elsewhere in this
    module (`test_missing_job_analysis_is_counted_as_unknown`)."""

    def __init__(self, *, application_id: ApplicationId) -> None:
        self.id = application_id
        self.sent_at: datetime | None = None


class TestApplicationsSentByWeekDefensiveGuard:
    def test_application_with_missing_sent_at_is_skipped_with_a_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        corrupted = _ApplicationWithMissingSentAt(application_id=ApplicationId.new())

        with caplog.at_level("WARNING"):
            result = _applications_sent_by_week([corrupted])  # type: ignore[list-item]

        assert result == []
        assert any(
            "stats.dashboard.sent_application_missing_sent_at" in record.getMessage()
            for record in caplog.records
        )
