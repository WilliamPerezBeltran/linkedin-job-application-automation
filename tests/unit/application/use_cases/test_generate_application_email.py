"""Unit tests for `GenerateApplicationEmail`.

Usa `InMemoryJobRepository`/`InMemoryJobAnalysisRepository` en memoria --
mismo patrón que `tests/unit/application/use_cases/test_select_best_cv.py`/
`test_analyze_job_post.py` -- un `FakeLLMProvider` (nunca llama a
Anthropic/OpenAI real) y un `FakeCVCatalog` simple, ambos implementando
`LLMProvider`/`CVCatalog` por structural typing (ver `docs/agents/AGENTS.md`
sección 8: unit tests nunca dependen de un LLM real).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import pytest

from app.application.cv.cv_profile import CVProfile
from app.application.dto.email_context import EmailContext
from app.application.dto.generated_email import GeneratedEmail
from app.application.dto.job_analysis_result import JobAnalysisResult
from app.application.use_cases.generate_application_email import (
    GenerateApplicationEmail,
    GenerateApplicationEmailOutcome,
)
from app.domain.entities.job import Job
from app.domain.entities.job_analysis import JobAnalysis
from app.domain.exceptions.invalid_state_transition_error import InvalidStateTransitionError
from app.domain.value_objects.job_id import JobId
from app.domain.value_objects.job_status import JobStatus
from app.infrastructure.cv.exceptions import CVNotFoundError
from app.infrastructure.llm.exceptions import LLMProviderError

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)


class FakeLLMProvider:
    """In-memory `LLMProvider`: returns a pre-configured `GeneratedEmail` (or
    raises a pre-configured exception) per call, and records the
    `EmailContext` it was called with."""

    def __init__(
        self,
        result: GeneratedEmail | None = None,
        error: Exception | None = None,
    ) -> None:
        self._result = result
        self._error = error
        self.generate_email_calls: list[EmailContext] = []

    def analyze_job(self, content: str) -> JobAnalysisResult:
        raise NotImplementedError("Not used by GenerateApplicationEmail tests.")

    def generate_email(self, context: EmailContext) -> GeneratedEmail:
        self.generate_email_calls.append(context)
        if self._error is not None:
            raise self._error
        assert self._result is not None
        return self._result


class FakeCVCatalog:
    """In-memory `CVCatalog`: returns a fixed summary per `cv_id`, or raises
    a pre-configured exception."""

    def __init__(
        self,
        summaries_by_cv_id: dict[str, str] | None = None,
        errors_by_cv_id: dict[str, Exception] | None = None,
    ) -> None:
        self._summaries_by_cv_id = summaries_by_cv_id or {}
        self._errors_by_cv_id = errors_by_cv_id or {}

    def list_cvs(self) -> list[CVProfile]:
        raise NotImplementedError("Not used by GenerateApplicationEmail tests.")

    def get_summary(self, cv_id: str) -> str:
        if cv_id in self._errors_by_cv_id:
            raise self._errors_by_cv_id[cv_id]
        return self._summaries_by_cv_id[cv_id]


class InMemoryJobRepository:
    """In-memory `JobRepository`, keyed by id and indexed by content_hash."""

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


def _make_cv_selected_job(*, content_hash: str, content: str = "We are hiring!") -> Job:
    job = Job.create(
        source="linkedin",
        author="Jane Recruiter",
        content=content,
        content_hash=content_hash,
        email=None,
        url=f"https://www.linkedin.com/feed/update/{content_hash}/",
        published_at=_NOW,
        scraped_at=_NOW,
        created_at=_NOW,
    )
    job.mark_analyzed()
    job.mark_relevant()
    job.mark_cv_selected()
    return job


def _make_job_analysis_with_cv(*, job_id: JobId, recommended_cv: str = "python") -> JobAnalysis:
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
    job_analysis.record_cv_recommendation(recommended_cv=recommended_cv, match_score=1.0)
    return job_analysis


class TestGenerateApplicationEmailGeneratesSuccessfully:
    def test_records_email_and_marks_job_email_generated(self) -> None:
        job = _make_cv_selected_job(content_hash="a" * 64)
        job_analysis = _make_job_analysis_with_cv(job_id=job.id)
        job_repository = InMemoryJobRepository()
        job_repository.seed(job)
        analysis_repository = InMemoryJobAnalysisRepository()
        analysis_repository.seed(job_analysis)
        cv_catalog = FakeCVCatalog(summaries_by_cv_id={"python": "Python backend engineer."})
        llm_provider = FakeLLMProvider(
            result=GeneratedEmail(subject="Application for Python role", body="Dear team...")
        )

        use_case = GenerateApplicationEmail(
            job_repository=job_repository,
            job_analysis_repository=analysis_repository,
            cv_catalog=cv_catalog,
            llm_provider=llm_provider,
        )
        result = use_case.execute()

        assert result.generated == 1
        assert result.failed_llm_calls == 0
        assert result.skipped_missing_analysis == 0
        assert result.skipped_missing_cv_summary == 0

        stored_job = job_repository.get_by_id(job.id)
        assert stored_job is not None
        assert stored_job.status == JobStatus.EMAIL_GENERATED

        stored_analysis = analysis_repository.get_by_job_id(job.id)
        assert stored_analysis is not None
        assert stored_analysis.subject == "Application for Python role"
        assert stored_analysis.generated_email == "Dear team..."

    def test_email_context_carries_the_resolved_cv_summary_not_a_path(self) -> None:
        job = _make_cv_selected_job(content_hash="b" * 64, content="Post body here")
        job_analysis = _make_job_analysis_with_cv(job_id=job.id, recommended_cv="ai")
        job_repository = InMemoryJobRepository()
        job_repository.seed(job)
        analysis_repository = InMemoryJobAnalysisRepository()
        analysis_repository.seed(job_analysis)
        cv_catalog = FakeCVCatalog(summaries_by_cv_id={"ai": "AI/ML engineer, 5 years."})
        llm_provider = FakeLLMProvider(result=GeneratedEmail(subject="subject", body="body"))

        use_case = GenerateApplicationEmail(
            job_repository=job_repository,
            job_analysis_repository=analysis_repository,
            cv_catalog=cv_catalog,
            llm_provider=llm_provider,
        )
        use_case.execute()

        assert len(llm_provider.generate_email_calls) == 1
        context = llm_provider.generate_email_calls[0]
        assert context.cv_summary == "AI/ML engineer, 5 years."
        assert context.job_type == "Python"
        assert context.seniority == "Senior"
        assert context.skills == ("Python", "FastAPI")
        assert context.languages == ("Python",)
        assert context.frameworks == ("FastAPI",)
        assert context.author == "Jane Recruiter"
        assert context.job_content == "Post body here"


class TestGenerateApplicationEmailHandlesLLMFailure:
    def test_job_stays_cv_selected_and_no_email_is_recorded(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        job = _make_cv_selected_job(content_hash="c" * 64)
        job_analysis = _make_job_analysis_with_cv(job_id=job.id)
        job_repository = InMemoryJobRepository()
        job_repository.seed(job)
        analysis_repository = InMemoryJobAnalysisRepository()
        analysis_repository.seed(job_analysis)
        cv_catalog = FakeCVCatalog(summaries_by_cv_id={"python": "Python backend engineer."})
        llm_provider = FakeLLMProvider(error=LLMProviderError("provider timed out"))

        use_case = GenerateApplicationEmail(
            job_repository=job_repository,
            job_analysis_repository=analysis_repository,
            cv_catalog=cv_catalog,
            llm_provider=llm_provider,
        )

        with caplog.at_level(logging.WARNING):
            result = use_case.execute()

        assert result.generated == 0
        assert result.failed_llm_calls == 1
        assert result.skipped_missing_analysis == 0
        assert result.skipped_missing_cv_summary == 0

        stored_job = job_repository.get_by_id(job.id)
        assert stored_job is not None
        assert stored_job.status == JobStatus.CV_SELECTED

        stored_analysis = analysis_repository.get_by_job_id(job.id)
        assert stored_analysis is not None
        assert stored_analysis.subject is None
        assert stored_analysis.generated_email is None

        assert any(
            record.levelno == logging.WARNING and str(job.id) in record.getMessage()
            for record in caplog.records
        )


class TestGenerateApplicationEmailHandlesMissingCVSummary:
    def test_does_not_abort_the_rest_of_the_batch(self, caplog: pytest.LogCaptureFixture) -> None:
        job_missing_cv = _make_cv_selected_job(content_hash="d" * 64)
        job_analysis_missing_cv = _make_job_analysis_with_cv(
            job_id=job_missing_cv.id, recommended_cv="ghost"
        )
        job_ok = _make_cv_selected_job(content_hash="e" * 64)
        job_analysis_ok = _make_job_analysis_with_cv(job_id=job_ok.id, recommended_cv="python")

        job_repository = InMemoryJobRepository()
        job_repository.seed(job_missing_cv)
        job_repository.seed(job_ok)
        analysis_repository = InMemoryJobAnalysisRepository()
        analysis_repository.seed(job_analysis_missing_cv)
        analysis_repository.seed(job_analysis_ok)
        cv_catalog = FakeCVCatalog(
            summaries_by_cv_id={"python": "Python backend engineer."},
            errors_by_cv_id={"ghost": CVNotFoundError("ghost")},
        )
        llm_provider = FakeLLMProvider(result=GeneratedEmail(subject="subject", body="body"))

        use_case = GenerateApplicationEmail(
            job_repository=job_repository,
            job_analysis_repository=analysis_repository,
            cv_catalog=cv_catalog,
            llm_provider=llm_provider,
        )

        with caplog.at_level(logging.WARNING):
            result = use_case.execute()

        assert result.generated == 1
        assert result.failed_llm_calls == 0
        assert result.skipped_missing_analysis == 0
        assert result.skipped_missing_cv_summary == 1

        stored_missing_cv_job = job_repository.get_by_id(job_missing_cv.id)
        assert stored_missing_cv_job is not None
        assert stored_missing_cv_job.status == JobStatus.CV_SELECTED

        stored_ok_job = job_repository.get_by_id(job_ok.id)
        assert stored_ok_job is not None
        assert stored_ok_job.status == JobStatus.EMAIL_GENERATED

        assert any(
            record.levelno == logging.WARNING and str(job_missing_cv.id) in record.getMessage()
            for record in caplog.records
        )


class TestGenerateApplicationEmailHandlesMissingJobAnalysis:
    def test_does_not_abort_the_rest_of_the_batch(self, caplog: pytest.LogCaptureFixture) -> None:
        job_without_analysis = _make_cv_selected_job(content_hash="f" * 64)
        job_with_analysis = _make_cv_selected_job(content_hash="g" * 64)
        job_analysis = _make_job_analysis_with_cv(job_id=job_with_analysis.id)

        job_repository = InMemoryJobRepository()
        job_repository.seed(job_without_analysis)
        job_repository.seed(job_with_analysis)
        analysis_repository = InMemoryJobAnalysisRepository()
        analysis_repository.seed(job_analysis)
        cv_catalog = FakeCVCatalog(summaries_by_cv_id={"python": "Python backend engineer."})
        llm_provider = FakeLLMProvider(result=GeneratedEmail(subject="subject", body="body"))

        use_case = GenerateApplicationEmail(
            job_repository=job_repository,
            job_analysis_repository=analysis_repository,
            cv_catalog=cv_catalog,
            llm_provider=llm_provider,
        )

        with caplog.at_level(logging.WARNING):
            result = use_case.execute()

        assert result.skipped_missing_analysis == 1
        assert result.generated == 1
        assert result.failed_llm_calls == 0
        assert result.skipped_missing_cv_summary == 0

        stored_without_analysis = job_repository.get_by_id(job_without_analysis.id)
        assert stored_without_analysis is not None
        assert stored_without_analysis.status == JobStatus.CV_SELECTED

        stored_with_analysis = job_repository.get_by_id(job_with_analysis.id)
        assert stored_with_analysis is not None
        assert stored_with_analysis.status == JobStatus.EMAIL_GENERATED

        assert any(
            record.levelno == logging.WARNING
            and str(job_without_analysis.id) in record.getMessage()
            for record in caplog.records
        )


class TestGenerateApplicationEmailOnlyProcessesCVSelectedJobs:
    def test_jobs_in_other_statuses_are_not_processed(self) -> None:
        relevant_job = Job.create(
            source="linkedin",
            author="Jane Recruiter",
            content="We are hiring a Senior Engineer, remote, apply now!",
            content_hash="h" * 64,
            email=None,
            url="https://www.linkedin.com/feed/update/h/",
            published_at=_NOW,
            scraped_at=_NOW,
            created_at=_NOW,
        )
        relevant_job.mark_analyzed()
        relevant_job.mark_relevant()

        analyzed_job = Job.create(
            source="linkedin",
            author="Jane Recruiter",
            content="We are hiring a Senior Engineer, remote, apply now!",
            content_hash="i" * 64,
            email=None,
            url="https://www.linkedin.com/feed/update/i/",
            published_at=_NOW,
            scraped_at=_NOW,
            created_at=_NOW,
        )
        analyzed_job.mark_analyzed()

        job_repository = InMemoryJobRepository()
        job_repository.seed(relevant_job)
        job_repository.seed(analyzed_job)
        analysis_repository = InMemoryJobAnalysisRepository()
        cv_catalog = FakeCVCatalog()
        llm_provider = FakeLLMProvider(result=GeneratedEmail(subject="s", body="b"))

        use_case = GenerateApplicationEmail(
            job_repository=job_repository,
            job_analysis_repository=analysis_repository,
            cv_catalog=cv_catalog,
            llm_provider=llm_provider,
        )
        result = use_case.execute()

        assert result.generated == 0
        assert result.failed_llm_calls == 0
        assert result.skipped_missing_analysis == 0
        assert result.skipped_missing_cv_summary == 0
        assert llm_provider.generate_email_calls == []

        stored_relevant = job_repository.get_by_id(relevant_job.id)
        assert stored_relevant is not None
        assert stored_relevant.status == JobStatus.RELEVANT
        stored_analyzed = job_repository.get_by_id(analyzed_job.id)
        assert stored_analyzed is not None
        assert stored_analyzed.status == JobStatus.ANALYZED


class TestGenerateApplicationEmailExecuteOne:
    """Unit tests for `GenerateApplicationEmail.execute_one`, called
    directly (not via `execute()`'s batch loop)."""

    def test_returns_generated_outcome_and_records_email(self) -> None:
        job = _make_cv_selected_job(content_hash="j" * 64)
        job_analysis = _make_job_analysis_with_cv(job_id=job.id)
        job_repository = InMemoryJobRepository()
        job_repository.seed(job)
        analysis_repository = InMemoryJobAnalysisRepository()
        analysis_repository.seed(job_analysis)
        cv_catalog = FakeCVCatalog(summaries_by_cv_id={"python": "Python backend engineer."})
        llm_provider = FakeLLMProvider(result=GeneratedEmail(subject="subject", body="body"))

        use_case = GenerateApplicationEmail(
            job_repository=job_repository,
            job_analysis_repository=analysis_repository,
            cv_catalog=cv_catalog,
            llm_provider=llm_provider,
        )
        outcome = use_case.execute_one(job)

        assert outcome is GenerateApplicationEmailOutcome.GENERATED
        assert job.status == JobStatus.EMAIL_GENERATED

    def test_returns_missing_analysis_outcome(self, caplog: pytest.LogCaptureFixture) -> None:
        job = _make_cv_selected_job(content_hash="k" * 64)
        job_repository = InMemoryJobRepository()
        job_repository.seed(job)
        analysis_repository = InMemoryJobAnalysisRepository()
        cv_catalog = FakeCVCatalog()
        llm_provider = FakeLLMProvider(result=GeneratedEmail(subject="subject", body="body"))

        use_case = GenerateApplicationEmail(
            job_repository=job_repository,
            job_analysis_repository=analysis_repository,
            cv_catalog=cv_catalog,
            llm_provider=llm_provider,
        )

        with caplog.at_level(logging.WARNING):
            outcome = use_case.execute_one(job)

        assert outcome is GenerateApplicationEmailOutcome.MISSING_ANALYSIS
        assert job.status == JobStatus.CV_SELECTED

    def test_cv_infrastructure_error_propagates_instead_of_being_caught(self) -> None:
        job = _make_cv_selected_job(content_hash="l" * 64)
        job_analysis = _make_job_analysis_with_cv(job_id=job.id, recommended_cv="ghost")
        job_repository = InMemoryJobRepository()
        job_repository.seed(job)
        analysis_repository = InMemoryJobAnalysisRepository()
        analysis_repository.seed(job_analysis)
        cv_catalog = FakeCVCatalog(errors_by_cv_id={"ghost": CVNotFoundError("ghost")})
        llm_provider = FakeLLMProvider(result=GeneratedEmail(subject="subject", body="body"))

        use_case = GenerateApplicationEmail(
            job_repository=job_repository,
            job_analysis_repository=analysis_repository,
            cv_catalog=cv_catalog,
            llm_provider=llm_provider,
        )

        with pytest.raises(CVNotFoundError):
            use_case.execute_one(job)

        assert job.status == JobStatus.CV_SELECTED

    def test_llm_provider_error_propagates_instead_of_being_caught(self) -> None:
        job = _make_cv_selected_job(content_hash="m" * 64)
        job_analysis = _make_job_analysis_with_cv(job_id=job.id)
        job_repository = InMemoryJobRepository()
        job_repository.seed(job)
        analysis_repository = InMemoryJobAnalysisRepository()
        analysis_repository.seed(job_analysis)
        cv_catalog = FakeCVCatalog(summaries_by_cv_id={"python": "Python backend engineer."})
        llm_provider = FakeLLMProvider(error=LLMProviderError("boom"))

        use_case = GenerateApplicationEmail(
            job_repository=job_repository,
            job_analysis_repository=analysis_repository,
            cv_catalog=cv_catalog,
            llm_provider=llm_provider,
        )

        with pytest.raises(LLMProviderError):
            use_case.execute_one(job)

        assert job.status == JobStatus.CV_SELECTED

    def test_invalid_state_transition_propagates_instead_of_being_caught(self) -> None:
        job = _make_cv_selected_job(content_hash="n" * 64)
        job.mark_email_generated()  # now EMAIL_GENERATED, not CV_SELECTED
        job_analysis = _make_job_analysis_with_cv(job_id=job.id)
        job_repository = InMemoryJobRepository()
        job_repository.seed(job)
        analysis_repository = InMemoryJobAnalysisRepository()
        analysis_repository.seed(job_analysis)
        cv_catalog = FakeCVCatalog(summaries_by_cv_id={"python": "Python backend engineer."})
        llm_provider = FakeLLMProvider(result=GeneratedEmail(subject="subject", body="body"))

        use_case = GenerateApplicationEmail(
            job_repository=job_repository,
            job_analysis_repository=analysis_repository,
            cv_catalog=cv_catalog,
            llm_provider=llm_provider,
        )

        with pytest.raises(InvalidStateTransitionError):
            use_case.execute_one(job)
