"""Unit tests for `CreateGmailDraft`.

Usa repositorios en memoria -- mismo patrón que
`test_generate_application_email.py` -- y un `FakeCVCatalog`/
`FakeEmailDraftRepository` simples, implementando `CVCatalog`/
`EmailDraftRepository` por structural typing (ver `docs/agents/AGENTS.md`
sección 8: unit tests nunca dependen de Gmail real).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.application.cv.cv_profile import CVProfile
from app.application.use_cases.create_gmail_draft import (
    CreateGmailDraft,
    InconsistentJobPipelineStateError,
)
from app.domain.entities.application import Application
from app.domain.entities.job import Job
from app.domain.entities.job_analysis import JobAnalysis
from app.domain.exceptions.invalid_state_transition_error import InvalidStateTransitionError
from app.domain.value_objects.email_address import EmailAddress
from app.domain.value_objects.job_id import JobId
from app.domain.value_objects.job_status import JobStatus
from app.infrastructure.cv.exceptions import CVCatalogNotFoundError

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)


class FakeCVCatalog:
    """In-memory `CVCatalog`: returns a fixed list of `CVProfile`, or raises
    a pre-configured exception."""

    def __init__(
        self,
        profiles: list[CVProfile] | None = None,
        error: Exception | None = None,
    ) -> None:
        self._profiles = profiles or []
        self._error = error

    def list_cvs(self) -> list[CVProfile]:
        if self._error is not None:
            raise self._error
        return self._profiles

    def get_summary(self, cv_id: str) -> str:
        raise NotImplementedError("Not used by CreateGmailDraft tests.")


class FakeEmailDraftRepository:
    """In-memory `EmailDraftRepository`: returns a pre-configured draft id
    (or raises a pre-configured exception), and records every call."""

    def __init__(self, draft_id: str = "draft-1", error: Exception | None = None) -> None:
        self._draft_id = draft_id
        self._error = error
        self.calls: list[dict[str, str]] = []

    def create_draft(self, *, to: EmailAddress, subject: str, body: str, cv_path: str) -> str:
        self.calls.append({"to": str(to), "subject": subject, "body": body, "cv_path": cv_path})
        if self._error is not None:
            raise self._error
        return self._draft_id


class InMemoryJobRepository:
    """In-memory `JobRepository`, keyed by id."""

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


class InMemoryJobAnalysisRepository:
    """In-memory `JobAnalysisRepository`, keyed by job_id."""

    def __init__(self) -> None:
        self._by_job_id: dict[JobId, JobAnalysis] = {}

    def save(self, job_analysis: JobAnalysis) -> None:
        self._by_job_id[job_analysis.job_id] = job_analysis

    def get_by_job_id(self, job_id: JobId) -> JobAnalysis | None:
        return self._by_job_id.get(job_id)

    def seed(self, job_analysis: JobAnalysis) -> None:
        self._by_job_id[job_analysis.job_id] = job_analysis


class InMemoryApplicationRepository:
    """In-memory `ApplicationRepository`, keyed by id and indexed by job_id."""

    def __init__(self) -> None:
        self._by_id: dict[object, Application] = {}

    def save(self, application: Application) -> None:
        self._by_id[application.id] = application

    def get_by_id(self, application_id: object) -> Application | None:
        return self._by_id.get(application_id)

    def get_by_job_id(self, job_id: JobId) -> Application | None:
        for application in self._by_id.values():
            if application.job_id == job_id:
                return application
        return None

    def seed(self, application: Application) -> None:
        self._by_id[application.id] = application


_DEFAULT_CONTACT_EMAIL = EmailAddress("recruiter@example.com")


def _make_email_generated_job(
    *, content_hash: str, email: EmailAddress | None = _DEFAULT_CONTACT_EMAIL
) -> Job:
    job = Job.create(
        source="linkedin",
        author="Jane Recruiter",
        content="We are hiring!",
        content_hash=content_hash,
        email=email,
        url=f"https://www.linkedin.com/feed/update/{content_hash}/",
        published_at=_NOW,
        scraped_at=_NOW,
        created_at=_NOW,
    )
    job.mark_analyzed()
    job.mark_relevant()
    job.mark_cv_selected()
    job.mark_email_generated()
    return job


def _make_complete_job_analysis(
    *,
    job_id: JobId,
    recommended_cv: str | None = "python",
    subject: str | None = "Application for Python role",
    generated_email: str | None = "Dear team...",
) -> JobAnalysis:
    job_analysis = JobAnalysis(
        job_id=job_id,
        job_type="Python",
        seniority="Senior",
        skills=("Python", "FastAPI"),
        languages=("Python",),
        frameworks=("FastAPI",),
        cloud=(),
        ai_related=False,
    )
    if recommended_cv is not None:
        job_analysis.record_cv_recommendation(recommended_cv=recommended_cv, match_score=1.0)
    if subject is not None and generated_email is not None:
        job_analysis.record_generated_email(subject=subject, body=generated_email)
    return job_analysis


def _make_python_cv_profile(cv_id: str = "python") -> CVProfile:
    return CVProfile(
        id=cv_id,
        file=f"cvs/python/{cv_id}.pdf",
        skills=("Python", "FastAPI"),
        summary="Python backend engineer.",
    )


class TestCreateGmailDraftCreatesNewDraft:
    def test_creates_application_and_marks_job_draft_created(self) -> None:
        job = _make_email_generated_job(content_hash="a" * 64)
        job_analysis = _make_complete_job_analysis(job_id=job.id)
        job_repository = InMemoryJobRepository()
        job_repository.seed(job)
        analysis_repository = InMemoryJobAnalysisRepository()
        analysis_repository.seed(job_analysis)
        application_repository = InMemoryApplicationRepository()
        cv_catalog = FakeCVCatalog(profiles=[_make_python_cv_profile()])
        email_draft_repository = FakeEmailDraftRepository(draft_id="gmail-draft-123")

        use_case = CreateGmailDraft(
            job_repository=job_repository,
            job_analysis_repository=analysis_repository,
            application_repository=application_repository,
            cv_catalog=cv_catalog,
            email_draft_repository=email_draft_repository,
        )
        application = use_case.execute_one(job)

        assert application.job_id == job.id
        assert application.email == EmailAddress("recruiter@example.com")
        assert application.subject == "Application for Python role"
        assert application.body == "Dear team..."
        assert application.cv_path == "cvs/python/python.pdf"
        assert application.gmail_draft_id == "gmail-draft-123"

        assert len(email_draft_repository.calls) == 1
        call = email_draft_repository.calls[0]
        assert call["to"] == "recruiter@example.com"
        assert call["subject"] == "Application for Python role"
        assert call["body"] == "Dear team..."
        assert call["cv_path"] == "cvs/python/python.pdf"

        stored_job = job_repository.get_by_id(job.id)
        assert stored_job is not None
        assert stored_job.status == JobStatus.DRAFT_CREATED

        stored_application = application_repository.get_by_job_id(job.id)
        assert stored_application == application


class TestCreateGmailDraftIsIdempotent:
    def test_reuses_existing_application_without_calling_gmail_again(self) -> None:
        job = _make_email_generated_job(content_hash="b" * 64)
        job.mark_draft_created()  # a previous run already completed this step
        job_analysis = _make_complete_job_analysis(job_id=job.id)
        job_repository = InMemoryJobRepository()
        job_repository.seed(job)
        analysis_repository = InMemoryJobAnalysisRepository()
        analysis_repository.seed(job_analysis)

        existing_application = Application.create(
            job_id=job.id,
            email=EmailAddress("recruiter@example.com"),
            subject="Application for Python role",
            body="Dear team...",
            cv_path="cvs/python/python.pdf",
            gmail_draft_id="gmail-draft-existing",
        )
        application_repository = InMemoryApplicationRepository()
        application_repository.seed(existing_application)

        cv_catalog = FakeCVCatalog(profiles=[_make_python_cv_profile()])
        email_draft_repository = FakeEmailDraftRepository()

        use_case = CreateGmailDraft(
            job_repository=job_repository,
            job_analysis_repository=analysis_repository,
            application_repository=application_repository,
            cv_catalog=cv_catalog,
            email_draft_repository=email_draft_repository,
        )
        application = use_case.execute_one(job)

        assert application == existing_application
        assert application.gmail_draft_id == "gmail-draft-existing"
        assert email_draft_repository.calls == []
        assert job.status == JobStatus.DRAFT_CREATED


class FailingApplicationRepository:
    """`ApplicationRepository` whose `save()` always raises -- simulates the
    `IntegrityError` `SQLAlchemyApplicationRepository.save`'s `flush()` would
    raise under the concurrent-request race documented in
    `app/presentation/api/routes/jobs.py::create_draft`. `get_by_job_id`
    always returns `None`: this fake only exists to exercise what happens
    *inside* `execute_one` when persistence fails after Gmail already
    created a real draft, not the router's own recovery (covered by
    `tests/unit/presentation/api/routes/test_jobs.py::TestCreateDraft`)."""

    def save(self, application: Application) -> None:
        raise RuntimeError("simulated IntegrityError from a concurrent save()")

    def get_by_id(self, application_id: object) -> Application | None:
        raise NotImplementedError("Not used by this test.")

    def get_by_job_id(self, job_id: JobId) -> Application | None:
        return None


class TestCreateGmailDraftLogsTheDraftIdBeforePersisting:
    """Regression test for a MEDIUM `code-reviewer` finding on
    `POST /api/jobs/{id}/create-draft`'s concurrency-race fix: the router's
    `IntegrityError` recovery closes the "500 no controlado" half of the
    original finding, but the `gmail_draft_id` of the *losing* request's
    draft never appeared anywhere -- it lived only in a local variable inside
    this method's stack frame, discarded the moment the exception propagated.
    `execute_one` now logs `job_id`/`gmail_draft_id` right after Gmail
    confirms the draft, before attempting `ApplicationRepository.save` --
    so even when `save()` fails, the id stays traceable in the logs."""

    def test_logs_job_id_and_gmail_draft_id_even_when_save_fails(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        job = _make_email_generated_job(content_hash="j" * 64)
        job_analysis = _make_complete_job_analysis(job_id=job.id)
        job_repository = InMemoryJobRepository()
        job_repository.seed(job)
        analysis_repository = InMemoryJobAnalysisRepository()
        analysis_repository.seed(job_analysis)
        application_repository = FailingApplicationRepository()
        cv_catalog = FakeCVCatalog(profiles=[_make_python_cv_profile()])
        email_draft_repository = FakeEmailDraftRepository(draft_id="gmail-draft-orphan")

        use_case = CreateGmailDraft(
            job_repository=job_repository,
            job_analysis_repository=analysis_repository,
            application_repository=application_repository,
            cv_catalog=cv_catalog,
            email_draft_repository=email_draft_repository,
        )

        with caplog.at_level("INFO"), pytest.raises(RuntimeError):
            use_case.execute_one(job)

        assert any(
            "gmail_draft_created" in record.getMessage()
            and str(job.id) in record.getMessage()
            and "gmail-draft-orphan" in record.getMessage()
            for record in caplog.records
        )


class TestCreateGmailDraftGuardsJobStatusBeforeSideEffects:
    """`execute_one` validates `job.status` before calling
    `EmailDraftRepository.create_draft` -- see the module docstring, point 2,
    for why this deliberately departs from the "let the entity's `mark_*`
    validate at the end" criterion used by `AnalyzeJobPost`/
    `GenerateApplicationEmail`."""

    def test_raises_before_touching_gmail_or_job_analysis_when_status_is_wrong(self) -> None:
        job = _make_email_generated_job(content_hash="z" * 64)
        job.mark_draft_created()  # now DRAFT_CREATED, not EMAIL_GENERATED
        job_repository = InMemoryJobRepository()
        job_repository.seed(job)
        # Deliberately no JobAnalysis seeded: if the status guard didn't run
        # first, the missing-JobAnalysis defensive check would fire instead,
        # masking the real problem (and, worse, only after already calling
        # Gmail in a version of this method without the early guard).
        analysis_repository = InMemoryJobAnalysisRepository()
        application_repository = InMemoryApplicationRepository()
        cv_catalog = FakeCVCatalog(profiles=[_make_python_cv_profile()])
        email_draft_repository = FakeEmailDraftRepository()

        use_case = CreateGmailDraft(
            job_repository=job_repository,
            job_analysis_repository=analysis_repository,
            application_repository=application_repository,
            cv_catalog=cv_catalog,
            email_draft_repository=email_draft_repository,
        )

        with pytest.raises(InvalidStateTransitionError):
            use_case.execute_one(job)

        assert email_draft_repository.calls == []
        assert application_repository.get_by_job_id(job.id) is None


class TestCreateGmailDraftHandlesInconsistentPipelineState:
    def test_raises_when_job_analysis_is_missing(self) -> None:
        job = _make_email_generated_job(content_hash="c" * 64)
        job_repository = InMemoryJobRepository()
        job_repository.seed(job)
        analysis_repository = InMemoryJobAnalysisRepository()
        application_repository = InMemoryApplicationRepository()
        cv_catalog = FakeCVCatalog(profiles=[_make_python_cv_profile()])
        email_draft_repository = FakeEmailDraftRepository()

        use_case = CreateGmailDraft(
            job_repository=job_repository,
            job_analysis_repository=analysis_repository,
            application_repository=application_repository,
            cv_catalog=cv_catalog,
            email_draft_repository=email_draft_repository,
        )

        with pytest.raises(InconsistentJobPipelineStateError):
            use_case.execute_one(job)

        assert job.status == JobStatus.EMAIL_GENERATED
        assert email_draft_repository.calls == []

    def test_raises_when_job_analysis_is_missing_generated_email_fields(self) -> None:
        job = _make_email_generated_job(content_hash="d" * 64)
        job_analysis = _make_complete_job_analysis(
            job_id=job.id, subject=None, generated_email=None
        )
        job_repository = InMemoryJobRepository()
        job_repository.seed(job)
        analysis_repository = InMemoryJobAnalysisRepository()
        analysis_repository.seed(job_analysis)
        application_repository = InMemoryApplicationRepository()
        cv_catalog = FakeCVCatalog(profiles=[_make_python_cv_profile()])
        email_draft_repository = FakeEmailDraftRepository()

        use_case = CreateGmailDraft(
            job_repository=job_repository,
            job_analysis_repository=analysis_repository,
            application_repository=application_repository,
            cv_catalog=cv_catalog,
            email_draft_repository=email_draft_repository,
        )

        with pytest.raises(InconsistentJobPipelineStateError):
            use_case.execute_one(job)

        assert email_draft_repository.calls == []

    def test_raises_when_job_has_no_email(self) -> None:
        job = _make_email_generated_job(content_hash="e" * 64, email=None)
        job_analysis = _make_complete_job_analysis(job_id=job.id)
        job_repository = InMemoryJobRepository()
        job_repository.seed(job)
        analysis_repository = InMemoryJobAnalysisRepository()
        analysis_repository.seed(job_analysis)
        application_repository = InMemoryApplicationRepository()
        cv_catalog = FakeCVCatalog(profiles=[_make_python_cv_profile()])
        email_draft_repository = FakeEmailDraftRepository()

        use_case = CreateGmailDraft(
            job_repository=job_repository,
            job_analysis_repository=analysis_repository,
            application_repository=application_repository,
            cv_catalog=cv_catalog,
            email_draft_repository=email_draft_repository,
        )

        with pytest.raises(InconsistentJobPipelineStateError):
            use_case.execute_one(job)

        assert email_draft_repository.calls == []

    def test_raises_when_recommended_cv_no_longer_exists_in_catalog(self) -> None:
        job = _make_email_generated_job(content_hash="f" * 64)
        job_analysis = _make_complete_job_analysis(job_id=job.id, recommended_cv="ghost")
        job_repository = InMemoryJobRepository()
        job_repository.seed(job)
        analysis_repository = InMemoryJobAnalysisRepository()
        analysis_repository.seed(job_analysis)
        application_repository = InMemoryApplicationRepository()
        cv_catalog = FakeCVCatalog(profiles=[_make_python_cv_profile()])
        email_draft_repository = FakeEmailDraftRepository()

        use_case = CreateGmailDraft(
            job_repository=job_repository,
            job_analysis_repository=analysis_repository,
            application_repository=application_repository,
            cv_catalog=cv_catalog,
            email_draft_repository=email_draft_repository,
        )

        with pytest.raises(InconsistentJobPipelineStateError):
            use_case.execute_one(job)

        assert email_draft_repository.calls == []


class TestCreateGmailDraftPropagatesInfrastructureErrors:
    def test_cv_infrastructure_error_propagates_instead_of_being_caught(self) -> None:
        job = _make_email_generated_job(content_hash="g" * 64)
        job_analysis = _make_complete_job_analysis(job_id=job.id)
        job_repository = InMemoryJobRepository()
        job_repository.seed(job)
        analysis_repository = InMemoryJobAnalysisRepository()
        analysis_repository.seed(job_analysis)
        application_repository = InMemoryApplicationRepository()
        cv_catalog = FakeCVCatalog(error=CVCatalogNotFoundError("config/cvs.yaml"))
        email_draft_repository = FakeEmailDraftRepository()

        use_case = CreateGmailDraft(
            job_repository=job_repository,
            job_analysis_repository=analysis_repository,
            application_repository=application_repository,
            cv_catalog=cv_catalog,
            email_draft_repository=email_draft_repository,
        )

        with pytest.raises(CVCatalogNotFoundError):
            use_case.execute_one(job)

        assert job.status == JobStatus.EMAIL_GENERATED

    def test_email_draft_repository_error_propagates_and_job_stays_email_generated(self) -> None:
        job = _make_email_generated_job(content_hash="h" * 64)
        job_analysis = _make_complete_job_analysis(job_id=job.id)
        job_repository = InMemoryJobRepository()
        job_repository.seed(job)
        analysis_repository = InMemoryJobAnalysisRepository()
        analysis_repository.seed(job_analysis)
        application_repository = InMemoryApplicationRepository()
        cv_catalog = FakeCVCatalog(profiles=[_make_python_cv_profile()])
        email_draft_repository = FakeEmailDraftRepository(error=RuntimeError("Gmail API down"))

        use_case = CreateGmailDraft(
            job_repository=job_repository,
            job_analysis_repository=analysis_repository,
            application_repository=application_repository,
            cv_catalog=cv_catalog,
            email_draft_repository=email_draft_repository,
        )

        with pytest.raises(RuntimeError):
            use_case.execute_one(job)

        assert job.status == JobStatus.EMAIL_GENERATED
        assert application_repository.get_by_job_id(job.id) is None

    def test_invalid_state_transition_propagates_instead_of_being_caught(self) -> None:
        job = _make_email_generated_job(content_hash="i" * 64)
        job.mark_draft_created()  # now DRAFT_CREATED, not EMAIL_GENERATED
        job_analysis = _make_complete_job_analysis(job_id=job.id)
        job_repository = InMemoryJobRepository()
        job_repository.seed(job)
        analysis_repository = InMemoryJobAnalysisRepository()
        analysis_repository.seed(job_analysis)
        application_repository = InMemoryApplicationRepository()
        cv_catalog = FakeCVCatalog(profiles=[_make_python_cv_profile()])
        email_draft_repository = FakeEmailDraftRepository()

        use_case = CreateGmailDraft(
            job_repository=job_repository,
            job_analysis_repository=analysis_repository,
            application_repository=application_repository,
            cv_catalog=cv_catalog,
            email_draft_repository=email_draft_repository,
        )

        with pytest.raises(InvalidStateTransitionError):
            use_case.execute_one(job)

        # The status guard fires before any external side effect: no Gmail
        # draft is created and no `Application` is persisted for a `Job`
        # that was never actually in `EMAIL_GENERATED`.
        assert email_draft_repository.calls == []
        assert application_repository.get_by_job_id(job.id) is None
