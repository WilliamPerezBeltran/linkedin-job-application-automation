"""Unit tests for `SelectBestCV`.

Usa `InMemoryJobRepository`/`InMemoryJobAnalysisRepository` en memoria --
mismo patrón que `tests/unit/application/use_cases/test_analyze_job_post.py`
-- y un `CVMatcher` real (`app/application/cv/matcher.py`) construido sobre
un `FakeCVCatalog` in-memory, ya que el matcher es lógica pura y
determinística (no hace falta fakearlo). El `FakeCVCatalog` implementa
`CVCatalog` por structural typing, sin tocar `config/cvs.yaml` real ni el
filesystem (`app/infrastructure/cv/**` está fuera del ownership de este
agente).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import pytest

from app.application.cv.cv_profile import CVProfile
from app.application.cv.matcher import CVMatcher
from app.application.use_cases.select_best_cv import SelectBestCV, SelectBestCVOutcome
from app.domain.entities.job import Job
from app.domain.entities.job_analysis import JobAnalysis
from app.domain.exceptions.invalid_state_transition_error import InvalidStateTransitionError
from app.domain.value_objects.job_id import JobId
from app.domain.value_objects.job_status import JobStatus

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)


class FakeCVCatalog:
    """In-memory `CVCatalog` with a small, fixed set of CVs for tests."""

    def __init__(self, profiles: list[CVProfile] | None = None) -> None:
        self._profiles = profiles or [
            CVProfile(
                id="java",
                file="cvs/java/william-java.pdf",
                skills=("Java", "Spring Boot", "Kafka"),
                summary="Java backend engineer.",
            ),
            CVProfile(
                id="python",
                file="cvs/python/william-python.pdf",
                skills=("Python", "FastAPI", "Django"),
                summary="Python backend engineer.",
            ),
            CVProfile(
                id="ai",
                file="cvs/ai/william-ai.pdf",
                skills=("Python", "LLM", "RAG", "Machine Learning"),
                summary="AI/ML engineer.",
            ),
        ]

    def list_cvs(self) -> list[CVProfile]:
        return list(self._profiles)

    def get_summary(self, cv_id: str) -> str:
        for profile in self._profiles:
            if profile.id == cv_id:
                return profile.summary
        raise KeyError(cv_id)


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


def _make_relevant_job(*, content_hash: str) -> Job:
    job = Job.create(
        source="linkedin",
        author="Jane Recruiter",
        content="We are hiring a Senior Engineer, remote, apply now!",
        content_hash=content_hash,
        email=None,
        url=f"https://www.linkedin.com/feed/update/{content_hash}/",
        published_at=_NOW,
        scraped_at=_NOW,
        created_at=_NOW,
    )
    job.mark_analyzed()
    job.mark_relevant()
    return job


def _make_job_analysis(*, job_id: JobId, skills: tuple[str, ...]) -> JobAnalysis:
    return JobAnalysis(
        job_id=job_id,
        job_type="Java",
        seniority="Senior",
        skills=skills,
        languages=(),
        frameworks=(),
        cloud=(),
        ai_related=False,
    )


class TestSelectBestCVMatchesAJobToAnExistingCV:
    def test_records_recommendation_and_marks_job_cv_selected(self) -> None:
        job = _make_relevant_job(content_hash="a" * 64)
        job_analysis = _make_job_analysis(job_id=job.id, skills=("Java", "Spring Boot", "Kafka"))
        job_repository = InMemoryJobRepository()
        job_repository.seed(job)
        analysis_repository = InMemoryJobAnalysisRepository()
        analysis_repository.seed(job_analysis)
        matcher = CVMatcher(FakeCVCatalog())

        use_case = SelectBestCV(
            job_repository=job_repository,
            job_analysis_repository=analysis_repository,
            cv_matcher=matcher,
        )
        result = use_case.execute()

        assert result.cv_selected == 1
        assert result.no_match == 0
        assert result.skipped_missing_analysis == 0
        stored_job = job_repository.get_by_id(job.id)
        assert stored_job is not None
        assert stored_job.status == JobStatus.CV_SELECTED
        stored_analysis = analysis_repository.get_by_job_id(job.id)
        assert stored_analysis is not None
        assert stored_analysis.recommended_cv == "java"
        assert stored_analysis.match_score == pytest.approx(1.0)


class TestSelectBestCVHandlesNoMatch:
    def test_job_stays_relevant_and_no_recommendation_is_recorded(self) -> None:
        job = _make_relevant_job(content_hash="b" * 64)
        job_analysis = _make_job_analysis(job_id=job.id, skills=("Elixir", "Phoenix"))
        job_repository = InMemoryJobRepository()
        job_repository.seed(job)
        analysis_repository = InMemoryJobAnalysisRepository()
        analysis_repository.seed(job_analysis)
        matcher = CVMatcher(FakeCVCatalog())

        use_case = SelectBestCV(
            job_repository=job_repository,
            job_analysis_repository=analysis_repository,
            cv_matcher=matcher,
        )
        result = use_case.execute()

        assert result.cv_selected == 0
        assert result.no_match == 1
        assert result.skipped_missing_analysis == 0
        stored_job = job_repository.get_by_id(job.id)
        assert stored_job is not None
        assert stored_job.status == JobStatus.RELEVANT
        stored_analysis = analysis_repository.get_by_job_id(job.id)
        assert stored_analysis is not None
        assert stored_analysis.recommended_cv is None
        assert stored_analysis.match_score is None


class TestSelectBestCVHandlesMissingJobAnalysis:
    def test_does_not_abort_the_rest_of_the_batch(self, caplog: pytest.LogCaptureFixture) -> None:
        job_without_analysis = _make_relevant_job(content_hash="c" * 64)
        job_with_analysis = _make_relevant_job(content_hash="d" * 64)
        job_analysis = _make_job_analysis(job_id=job_with_analysis.id, skills=("Python", "FastAPI"))
        job_repository = InMemoryJobRepository()
        job_repository.seed(job_without_analysis)
        job_repository.seed(job_with_analysis)
        analysis_repository = InMemoryJobAnalysisRepository()
        analysis_repository.seed(job_analysis)
        matcher = CVMatcher(FakeCVCatalog())

        use_case = SelectBestCV(
            job_repository=job_repository,
            job_analysis_repository=analysis_repository,
            cv_matcher=matcher,
        )

        with caplog.at_level(logging.WARNING):
            result = use_case.execute()

        assert result.skipped_missing_analysis == 1
        assert result.cv_selected == 1
        assert result.no_match == 0
        stored_job_without_analysis = job_repository.get_by_id(job_without_analysis.id)
        assert stored_job_without_analysis is not None
        assert stored_job_without_analysis.status == JobStatus.RELEVANT
        stored_job_with_analysis = job_repository.get_by_id(job_with_analysis.id)
        assert stored_job_with_analysis is not None
        assert stored_job_with_analysis.status == JobStatus.CV_SELECTED
        assert any(
            record.levelno == logging.WARNING
            and str(job_without_analysis.id) in record.getMessage()
            for record in caplog.records
        )


class TestSelectBestCVOnlyProcessesRelevantJobs:
    def test_jobs_in_other_statuses_are_not_processed(self) -> None:
        scraped_job = Job.create(
            source="linkedin",
            author="Jane Recruiter",
            content="We are hiring a Senior Engineer, remote, apply now!",
            content_hash="e" * 64,
            email=None,
            url="https://www.linkedin.com/feed/update/e/",
            published_at=_NOW,
            scraped_at=_NOW,
            created_at=_NOW,
        )
        analyzed_job = Job.create(
            source="linkedin",
            author="Jane Recruiter",
            content="We are hiring a Senior Engineer, remote, apply now!",
            content_hash="f" * 64,
            email=None,
            url="https://www.linkedin.com/feed/update/f/",
            published_at=_NOW,
            scraped_at=_NOW,
            created_at=_NOW,
        )
        analyzed_job.mark_analyzed()
        job_repository = InMemoryJobRepository()
        job_repository.seed(scraped_job)
        job_repository.seed(analyzed_job)
        analysis_repository = InMemoryJobAnalysisRepository()
        matcher = CVMatcher(FakeCVCatalog())

        use_case = SelectBestCV(
            job_repository=job_repository,
            job_analysis_repository=analysis_repository,
            cv_matcher=matcher,
        )
        result = use_case.execute()

        assert result.cv_selected == 0
        assert result.no_match == 0
        assert result.skipped_missing_analysis == 0
        stored_scraped = job_repository.get_by_id(scraped_job.id)
        assert stored_scraped is not None
        assert stored_scraped.status == JobStatus.SCRAPED
        stored_analyzed = job_repository.get_by_id(analyzed_job.id)
        assert stored_analyzed is not None
        assert stored_analyzed.status == JobStatus.ANALYZED


class TestSelectBestCVExecuteOne:
    """Unit tests for `SelectBestCV.execute_one`, called directly (not via
    `execute()`'s batch loop)."""

    def test_returns_cv_selected_outcome(self) -> None:
        job = _make_relevant_job(content_hash="g" * 64)
        job_analysis = _make_job_analysis(job_id=job.id, skills=("Java", "Spring Boot", "Kafka"))
        job_repository = InMemoryJobRepository()
        job_repository.seed(job)
        analysis_repository = InMemoryJobAnalysisRepository()
        analysis_repository.seed(job_analysis)
        matcher = CVMatcher(FakeCVCatalog())

        use_case = SelectBestCV(
            job_repository=job_repository,
            job_analysis_repository=analysis_repository,
            cv_matcher=matcher,
        )
        outcome = use_case.execute_one(job)

        assert outcome is SelectBestCVOutcome.CV_SELECTED
        assert job.status == JobStatus.CV_SELECTED

    def test_returns_no_match_outcome(self) -> None:
        job = _make_relevant_job(content_hash="h" * 64)
        job_analysis = _make_job_analysis(job_id=job.id, skills=("Elixir", "Phoenix"))
        job_repository = InMemoryJobRepository()
        job_repository.seed(job)
        analysis_repository = InMemoryJobAnalysisRepository()
        analysis_repository.seed(job_analysis)
        matcher = CVMatcher(FakeCVCatalog())

        use_case = SelectBestCV(
            job_repository=job_repository,
            job_analysis_repository=analysis_repository,
            cv_matcher=matcher,
        )
        outcome = use_case.execute_one(job)

        assert outcome is SelectBestCVOutcome.NO_MATCH
        assert job.status == JobStatus.RELEVANT

    def test_returns_missing_analysis_outcome(self, caplog: pytest.LogCaptureFixture) -> None:
        job = _make_relevant_job(content_hash="i" * 64)
        job_repository = InMemoryJobRepository()
        job_repository.seed(job)
        analysis_repository = InMemoryJobAnalysisRepository()
        matcher = CVMatcher(FakeCVCatalog())

        use_case = SelectBestCV(
            job_repository=job_repository,
            job_analysis_repository=analysis_repository,
            cv_matcher=matcher,
        )

        with caplog.at_level(logging.WARNING):
            outcome = use_case.execute_one(job)

        assert outcome is SelectBestCVOutcome.MISSING_ANALYSIS
        assert job.status == JobStatus.RELEVANT

    def test_invalid_state_transition_propagates_instead_of_being_caught(self) -> None:
        job = Job.create(
            source="linkedin",
            author="Jane Recruiter",
            content="We are hiring a Senior Engineer, remote, apply now!",
            content_hash="j" * 64,
            email=None,
            url="https://www.linkedin.com/feed/update/j/",
            published_at=_NOW,
            scraped_at=_NOW,
            created_at=_NOW,
        )  # still SCRAPED, not RELEVANT
        job_analysis = _make_job_analysis(job_id=job.id, skills=("Java",))
        job_repository = InMemoryJobRepository()
        job_repository.seed(job)
        analysis_repository = InMemoryJobAnalysisRepository()
        analysis_repository.seed(job_analysis)
        matcher = CVMatcher(FakeCVCatalog())

        use_case = SelectBestCV(
            job_repository=job_repository,
            job_analysis_repository=analysis_repository,
            cv_matcher=matcher,
        )

        with pytest.raises(InvalidStateTransitionError):
            use_case.execute_one(job)
