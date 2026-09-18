"""Unit tests for `AnalyzeJobPost`.

Usa un `FakeLLMProvider` (nunca llama a Anthropic/OpenAI real) y
`InMemoryJobRepository`/`InMemoryJobAnalysisRepository` en memoria,
implementando `LLMProvider`/`JobRepository`/`JobAnalysisRepository` por
structural typing -- mismo patrón que
`tests/unit/application/use_cases/test_collect_feed_posts.py` (ver
`docs/agents/AGENTS.md` sección 8: unit tests nunca dependen de un LLM real).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import pytest

from app.application.dto.email_context import EmailContext
from app.application.dto.generated_email import GeneratedEmail
from app.application.dto.job_analysis_result import JobAnalysisResult
from app.application.use_cases.analyze_job_post import AnalyzeJobPost, AnalyzeJobPostOutcome
from app.domain.entities.job import Job
from app.domain.entities.job_analysis import JobAnalysis
from app.domain.exceptions.invalid_state_transition_error import InvalidStateTransitionError
from app.domain.value_objects.job_id import JobId
from app.domain.value_objects.job_status import JobStatus
from app.infrastructure.llm.exceptions import LLMProviderError

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)


class FakeLLMProvider:
    """In-memory `LLMProvider`: returns pre-configured results keyed by
    job content, or raises a pre-configured exception, and counts calls."""

    def __init__(
        self,
        results_by_content: dict[str, JobAnalysisResult] | None = None,
        errors_by_content: dict[str, Exception] | None = None,
    ) -> None:
        self._results_by_content = results_by_content or {}
        self._errors_by_content = errors_by_content or {}
        self.analyze_job_calls: list[str] = []

    def analyze_job(self, content: str) -> JobAnalysisResult:
        self.analyze_job_calls.append(content)
        if content in self._errors_by_content:
            raise self._errors_by_content[content]
        return self._results_by_content[content]

    def generate_email(self, context: EmailContext) -> GeneratedEmail:
        raise NotImplementedError("Not used by AnalyzeJobPost tests.")


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


def _make_job(
    *, content: str, content_hash: str = "a" * 64, status: JobStatus = JobStatus.SCRAPED
) -> Job:
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
    if status is JobStatus.ANALYZED:
        job.mark_analyzed()
    return job


def _make_result(**overrides: object) -> JobAnalysisResult:
    defaults: dict[str, object] = {
        "is_job": True,
        "job_type": "Python",
        "seniority": "Senior",
        "skills": ("Python", "FastAPI"),
        "languages": ("Python",),
        "frameworks": ("FastAPI",),
        "cloud": (),
        "ai_related": False,
        "email_addresses": (),
        "confidence": 0.9,
    }
    defaults.update(overrides)
    return JobAnalysisResult(**defaults)  # type: ignore[arg-type]


class TestAnalyzeJobPostSkipsPostsThatFailTheCandidateFilter:
    def test_never_calls_the_llm_and_marks_not_relevant(self) -> None:
        # Content too short and without any job keyword: fails the cheap
        # candidate filter before ever reaching the LLM.
        job = _make_job(content="lorem ipsum dolor sit amet, nothing relevant here at all")
        repository = InMemoryJobRepository()
        repository.seed(job)
        analysis_repository = InMemoryJobAnalysisRepository()
        llm_provider = FakeLLMProvider()

        use_case = AnalyzeJobPost(
            job_repository=repository,
            job_analysis_repository=analysis_repository,
            llm_provider=llm_provider,
        )
        result = use_case.execute()

        assert llm_provider.analyze_job_calls == []
        assert result.skipped_by_filter == 1
        assert result.analyzed == 0
        assert result.not_relevant == 0
        stored_job = repository.get_by_id(job.id)
        assert stored_job is not None
        assert stored_job.status == JobStatus.NOT_RELEVANT


class TestAnalyzeJobPostHandlesLLMSayingNotAJob:
    def test_marks_not_relevant_without_creating_a_job_analysis(self) -> None:
        content = "We are hiring a Senior Python Engineer, remote, apply now!"
        job = _make_job(content=content, content_hash="b" * 64)
        repository = InMemoryJobRepository()
        repository.seed(job)
        analysis_repository = InMemoryJobAnalysisRepository()
        llm_provider = FakeLLMProvider(results_by_content={content: _make_result(is_job=False)})

        use_case = AnalyzeJobPost(
            job_repository=repository,
            job_analysis_repository=analysis_repository,
            llm_provider=llm_provider,
        )
        result = use_case.execute()

        assert llm_provider.analyze_job_calls == [content]
        assert result.analyzed == 1
        assert result.not_relevant == 1
        assert result.relevant == 0
        stored_job = repository.get_by_id(job.id)
        assert stored_job is not None
        assert stored_job.status == JobStatus.NOT_RELEVANT
        assert analysis_repository.get_by_job_id(job.id) is None


class TestAnalyzeJobPostHandlesLLMSayingIsAJob:
    def test_persists_job_analysis_and_marks_relevant(self) -> None:
        content = "We are hiring a Senior Python Engineer, remote, apply now!"
        job = _make_job(content=content, content_hash="c" * 64)
        repository = InMemoryJobRepository()
        repository.seed(job)
        analysis_repository = InMemoryJobAnalysisRepository()
        llm_provider = FakeLLMProvider(
            results_by_content={
                content: _make_result(
                    is_job=True,
                    job_type="Python",
                    seniority="Senior",
                    skills=("Python", "FastAPI"),
                    languages=("Python",),
                    frameworks=("FastAPI",),
                    cloud=("AWS",),
                    ai_related=False,
                )
            }
        )

        use_case = AnalyzeJobPost(
            job_repository=repository,
            job_analysis_repository=analysis_repository,
            llm_provider=llm_provider,
        )
        result = use_case.execute()

        assert result.analyzed == 1
        assert result.relevant == 1
        assert result.not_relevant == 0
        stored_job = repository.get_by_id(job.id)
        assert stored_job is not None
        assert stored_job.status == JobStatus.RELEVANT
        stored_analysis = analysis_repository.get_by_job_id(job.id)
        assert stored_analysis is not None
        assert stored_analysis.job_type == "Python"
        assert stored_analysis.seniority == "Senior"
        assert stored_analysis.skills == ("Python", "FastAPI")
        assert stored_analysis.languages == ("Python",)
        assert stored_analysis.frameworks == ("FastAPI",)
        assert stored_analysis.cloud == ("AWS",)
        assert stored_analysis.ai_related is False


class TestAnalyzeJobPostRecordsExtractedEmail:
    def test_sets_job_email_from_the_first_extracted_address(self) -> None:
        content = "We are hiring a Senior Python Engineer, remote, apply now! Send your CV."
        job = _make_job(content=content, content_hash="d" * 64)
        repository = InMemoryJobRepository()
        repository.seed(job)
        analysis_repository = InMemoryJobAnalysisRepository()
        llm_provider = FakeLLMProvider(
            results_by_content={
                content: _make_result(
                    email_addresses=("recruiter@example.com", "second@example.com")
                )
            }
        )

        use_case = AnalyzeJobPost(
            job_repository=repository,
            job_analysis_repository=analysis_repository,
            llm_provider=llm_provider,
        )
        use_case.execute()

        stored_job = repository.get_by_id(job.id)
        assert stored_job is not None
        assert stored_job.email is not None
        assert str(stored_job.email) == "recruiter@example.com"

    def test_malformed_extracted_email_is_dropped_without_aborting_processing(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        content = "We are hiring a Senior Python Engineer, remote, apply now! Send your CV."
        job = _make_job(content=content, content_hash="h" * 64)
        repository = InMemoryJobRepository()
        repository.seed(job)
        analysis_repository = InMemoryJobAnalysisRepository()
        llm_provider = FakeLLMProvider(
            results_by_content={content: _make_result(email_addresses=("not-an-email",))}
        )

        use_case = AnalyzeJobPost(
            job_repository=repository,
            job_analysis_repository=analysis_repository,
            llm_provider=llm_provider,
        )

        with caplog.at_level(logging.WARNING):
            result = use_case.execute()

        assert result.relevant == 1
        stored_job = repository.get_by_id(job.id)
        assert stored_job is not None
        assert stored_job.email is None
        assert stored_job.status == JobStatus.RELEVANT
        assert any(
            record.levelno == logging.WARNING and str(job.id) in record.getMessage()
            for record in caplog.records
        )

    def test_email_is_recorded_even_when_the_post_is_not_relevant(self) -> None:
        """Documents the current design decision: `record_extracted_email`
        runs before the `is_job` branch, so the email is persisted on the
        `Job` regardless of the final relevance outcome (see
        `analyze_job_post.py` module docstring / `Job.record_extracted_email`
        docstring: it's orthogonal data, without an associated status
        transition). If this decision is revisited, this test documents the
        behavior being changed, rather than a regression slipping in
        silently.
        """
        content = "We are hiring a Senior Python Engineer, remote, apply now! Send your CV."
        job = _make_job(content=content, content_hash="i" * 64)
        repository = InMemoryJobRepository()
        repository.seed(job)
        analysis_repository = InMemoryJobAnalysisRepository()
        llm_provider = FakeLLMProvider(
            results_by_content={
                content: _make_result(is_job=False, email_addresses=("recruiter@example.com",))
            }
        )

        use_case = AnalyzeJobPost(
            job_repository=repository,
            job_analysis_repository=analysis_repository,
            llm_provider=llm_provider,
        )
        use_case.execute()

        stored_job = repository.get_by_id(job.id)
        assert stored_job is not None
        assert stored_job.status == JobStatus.NOT_RELEVANT
        assert stored_job.email is not None
        assert str(stored_job.email) == "recruiter@example.com"


class TestAnalyzeJobPostSurvivesLLMProviderFailures:
    def test_job_stays_scraped_and_batch_continues(self, caplog: pytest.LogCaptureFixture) -> None:
        failing_content = "We are hiring a Senior Python Engineer, remote, apply now!"
        succeeding_content = "We are hiring a Java Backend Engineer, hybrid, apply now!"
        failing_job = _make_job(content=failing_content, content_hash="e" * 64)
        succeeding_job = _make_job(content=succeeding_content, content_hash="f" * 64)
        repository = InMemoryJobRepository()
        repository.seed(failing_job)
        repository.seed(succeeding_job)
        analysis_repository = InMemoryJobAnalysisRepository()
        llm_provider = FakeLLMProvider(
            results_by_content={succeeding_content: _make_result(job_type="Java")},
            errors_by_content={failing_content: LLMProviderError("boom")},
        )

        use_case = AnalyzeJobPost(
            job_repository=repository,
            job_analysis_repository=analysis_repository,
            llm_provider=llm_provider,
        )

        with caplog.at_level(logging.WARNING):
            result = use_case.execute()

        assert result.failed_llm_calls == 1
        assert result.analyzed == 1
        assert result.relevant == 1
        stored_failing_job = repository.get_by_id(failing_job.id)
        assert stored_failing_job is not None
        assert stored_failing_job.status == JobStatus.SCRAPED
        stored_succeeding_job = repository.get_by_id(succeeding_job.id)
        assert stored_succeeding_job is not None
        assert stored_succeeding_job.status == JobStatus.RELEVANT
        assert any(
            record.levelno == logging.WARNING and str(failing_job.id) in record.getMessage()
            for record in caplog.records
        )


class TestAnalyzeJobPostOnlyProcessesScrapedJobs:
    def test_a_job_already_analyzed_is_not_reprocessed(self) -> None:
        already_analyzed_content = "We are hiring a Senior Python Engineer, remote, apply now!"
        already_analyzed_job = _make_job(
            content=already_analyzed_content,
            content_hash="g" * 64,
            status=JobStatus.ANALYZED,
        )
        repository = InMemoryJobRepository()
        repository.seed(already_analyzed_job)
        analysis_repository = InMemoryJobAnalysisRepository()
        llm_provider = FakeLLMProvider()

        use_case = AnalyzeJobPost(
            job_repository=repository,
            job_analysis_repository=analysis_repository,
            llm_provider=llm_provider,
        )
        result = use_case.execute()

        assert llm_provider.analyze_job_calls == []
        assert result.analyzed == 0
        assert result.skipped_by_filter == 0
        stored_job = repository.get_by_id(already_analyzed_job.id)
        assert stored_job is not None
        assert stored_job.status == JobStatus.ANALYZED


class TestAnalyzeJobPostExecuteOne:
    """Unit tests for `AnalyzeJobPost.execute_one`, called directly (not via
    `execute()`'s batch loop) -- mismo comportamiento que `execute()` ya
    ejerce indirectamente, pero acá se afirma explícitamente el valor de
    retorno (`AnalyzeJobPostOutcome`) y que las excepciones de dominio/LLM se
    dejan propagar sin atrapar (a diferencia de `execute()`)."""

    def test_returns_relevant_outcome_and_persists_job_analysis(self) -> None:
        content = "We are hiring a Senior Python Engineer, remote, apply now!"
        job = _make_job(content=content, content_hash="j" * 64)
        repository = InMemoryJobRepository()
        repository.seed(job)
        analysis_repository = InMemoryJobAnalysisRepository()
        llm_provider = FakeLLMProvider(results_by_content={content: _make_result(is_job=True)})

        use_case = AnalyzeJobPost(
            job_repository=repository,
            job_analysis_repository=analysis_repository,
            llm_provider=llm_provider,
        )
        outcome = use_case.execute_one(job)

        assert outcome is AnalyzeJobPostOutcome.RELEVANT
        assert job.status == JobStatus.RELEVANT
        assert analysis_repository.get_by_job_id(job.id) is not None

    def test_returns_not_relevant_outcome_when_llm_says_not_a_job(self) -> None:
        content = "We are hiring a Senior Python Engineer, remote, apply now!"
        job = _make_job(content=content, content_hash="k" * 64)
        repository = InMemoryJobRepository()
        repository.seed(job)
        analysis_repository = InMemoryJobAnalysisRepository()
        llm_provider = FakeLLMProvider(results_by_content={content: _make_result(is_job=False)})

        use_case = AnalyzeJobPost(
            job_repository=repository,
            job_analysis_repository=analysis_repository,
            llm_provider=llm_provider,
        )
        outcome = use_case.execute_one(job)

        assert outcome is AnalyzeJobPostOutcome.NOT_RELEVANT
        assert job.status == JobStatus.NOT_RELEVANT

    def test_returns_skipped_by_filter_outcome_without_calling_the_llm(self) -> None:
        job = _make_job(content="lorem ipsum dolor sit amet, nothing relevant here at all")
        repository = InMemoryJobRepository()
        repository.seed(job)
        analysis_repository = InMemoryJobAnalysisRepository()
        llm_provider = FakeLLMProvider()

        use_case = AnalyzeJobPost(
            job_repository=repository,
            job_analysis_repository=analysis_repository,
            llm_provider=llm_provider,
        )
        outcome = use_case.execute_one(job)

        assert outcome is AnalyzeJobPostOutcome.SKIPPED_BY_FILTER
        assert llm_provider.analyze_job_calls == []
        assert job.status == JobStatus.NOT_RELEVANT

    def test_llm_provider_error_propagates_instead_of_being_caught(self) -> None:
        content = "We are hiring a Senior Python Engineer, remote, apply now!"
        job = _make_job(content=content, content_hash="l" * 64)
        repository = InMemoryJobRepository()
        repository.seed(job)
        analysis_repository = InMemoryJobAnalysisRepository()
        llm_provider = FakeLLMProvider(errors_by_content={content: LLMProviderError("boom")})

        use_case = AnalyzeJobPost(
            job_repository=repository,
            job_analysis_repository=analysis_repository,
            llm_provider=llm_provider,
        )

        with pytest.raises(LLMProviderError):
            use_case.execute_one(job)

        # No se persistió ningún cambio: el Job sigue en SCRAPED.
        stored_job = repository.get_by_id(job.id)
        assert stored_job is not None
        assert stored_job.status == JobStatus.SCRAPED

    def test_invalid_state_transition_propagates_instead_of_being_caught(self) -> None:
        content = "We are hiring a Senior Python Engineer, remote, apply now!"
        job = _make_job(content=content, content_hash="m" * 64, status=JobStatus.ANALYZED)
        repository = InMemoryJobRepository()
        repository.seed(job)
        analysis_repository = InMemoryJobAnalysisRepository()
        llm_provider = FakeLLMProvider(results_by_content={content: _make_result(is_job=True)})

        use_case = AnalyzeJobPost(
            job_repository=repository,
            job_analysis_repository=analysis_repository,
            llm_provider=llm_provider,
        )

        with pytest.raises(InvalidStateTransitionError):
            use_case.execute_one(job)
