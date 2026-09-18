"""Unit tests for `run_daily_pipeline`
(`app/presentation/scheduler/jobs.py`).

Ejercita únicamente la función pura de orquestación -- nunca
`run_scheduler`/APScheduler ni ninguna dependencia real (LinkedIn, LLM,
Gmail, PostgreSQL). Reusa el mismo patrón de fakes in-memory que
`tests/unit/application/use_cases/test_collect_feed_posts.py`/
`test_select_best_cv.py`/`test_generate_application_email.py`, y un
`_FakeJobPipelineSession` -- un context manager trivial que siempre
entrega el mismo par `(job_repository, job_analysis_repository)` -- como
`JobPipelineSessionFactory` de test, ya que los fakes in-memory no tienen
ningún concepto real de `Session`/commit que aislar entre etapas (ese
aislamiento se ejercita contra PostgreSQL real en
`tests/integration/test_scheduler_daily_pipeline.py`).
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError

from app.application.cv.cv_profile import CVProfile
from app.application.cv.matcher import CVMatcher
from app.application.dto.email_context import EmailContext
from app.application.dto.generated_email import GeneratedEmail
from app.application.dto.job_analysis_result import JobAnalysisResult
from app.application.dto.raw_feed_post import RawFeedPost
from app.domain.entities.job import Job
from app.domain.entities.job_analysis import JobAnalysis
from app.domain.value_objects.job_id import JobId
from app.domain.value_objects.job_status import JobStatus
from app.infrastructure.cv.exceptions import CVCatalogNotFoundError
from app.infrastructure.linkedin.exceptions import LinkedInAuthenticationError
from app.infrastructure.llm.exceptions import LLMProviderError
from app.presentation.scheduler.jobs import JobPipelineRepositories, run_daily_pipeline

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 9, 17, 8, 0, tzinfo=UTC)


class FakeFeedCollector:
    """In-memory `FeedCollector`: returns fixed posts, or raises a
    pre-configured exception (never real Playwright/LinkedIn)."""

    def __init__(
        self, posts: list[RawFeedPost] | None = None, error: Exception | None = None
    ) -> None:
        self._posts = posts or []
        self._error = error

    def collect(self) -> list[RawFeedPost]:
        if self._error is not None:
            raise self._error
        return list(self._posts)


class FakeLLMProvider:
    """In-memory `LLMProvider`: always reports the post as a relevant Java
    job and always returns a fixed generated email (never a real
    OpenAI/Anthropic call)."""

    def analyze_job(self, content: str) -> JobAnalysisResult:
        return JobAnalysisResult(
            is_job=True,
            job_type="Java",
            seniority="Senior",
            skills=("Java", "Spring Boot"),
            languages=("Java",),
            frameworks=("Spring Boot",),
            cloud=(),
            ai_related=False,
            email_addresses=(),
            confidence=0.9,
        )

    def generate_email(self, context: EmailContext) -> GeneratedEmail:
        return GeneratedEmail(subject="Application for Java role", body="Dear team...")


class FakeCVCatalog:
    """In-memory `CVCatalog` with a single `java` CV, matching
    `FakeLLMProvider`'s detected skills."""

    def __init__(self, broken: bool = False) -> None:
        self._broken = broken

    def list_cvs(self) -> list[CVProfile]:
        if self._broken:
            raise CVCatalogNotFoundError("config/cvs.yaml")
        return [
            CVProfile(
                id="java",
                file="cvs/java/william-java.pdf",
                skills=("Java", "Spring Boot", "Kafka"),
                summary="Java backend engineer.",
            )
        ]

    def get_summary(self, cv_id: str) -> str:
        if self._broken:
            raise CVCatalogNotFoundError("config/cvs.yaml")
        return "Java backend engineer."


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


class _RaisesOnceThenWorksJobRepository(InMemoryJobRepository):
    """`JobRepository` that raises `IntegrityError` on its first `save()`
    call only, then behaves like a normal `InMemoryJobRepository`.

    Simula una carrera real (dos invocaciones concurrentes -- este
    scheduler y, p. ej., un `POST /scrape` manual -- intentando persistir
    el mismo `content_hash`, ver `app/presentation/api/routes/scrape.py`):
    solo el `save()` del `Job` recién scrapeado (la etapa de collect) choca
    con la carrera -- las etapas siguientes, sobre jobs ya existentes de un
    backlog previo, no se ven afectadas por esa misma condición."""

    def __init__(self) -> None:
        super().__init__()
        self._has_raised = False

    def save(self, job: Job) -> None:
        if not self._has_raised:
            self._has_raised = True
            raise IntegrityError("INSERT INTO jobs ...", {}, Exception("duplicate content_hash"))
        super().save(job)


def _make_raw_post(*, content_hash: str) -> RawFeedPost:
    return RawFeedPost(
        author="Jane Recruiter",
        content="We are hiring a Senior Java Engineer, remote, apply now!",
        content_hash=content_hash,
        url=f"https://www.linkedin.com/feed/update/{content_hash}/",
        published_at=_NOW,
    )


def _make_cv_selected_job(*, content_hash: str) -> Job:
    job = Job.create(
        source="linkedin",
        author="Jane Recruiter",
        content="We are hiring a Senior Java Engineer, remote, apply now!",
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


def _make_job_analysis_with_cv(*, job_id: JobId, recommended_cv: str = "java") -> JobAnalysis:
    job_analysis = JobAnalysis(
        job_id=job_id,
        job_type="Java",
        seniority="Senior",
        skills=("Java", "Spring Boot"),
        languages=("Java",),
        frameworks=("Spring Boot",),
        cloud=(),
        ai_related=False,
    )
    job_analysis.record_cv_recommendation(recommended_cv=recommended_cv, match_score=1.0)
    return job_analysis


class _FakeJobPipelineSession:
    """`JobPipelineSessionFactory` test double: always yields the same
    `(job_repository, job_analysis_repository)` pair, never commits/rolls
    back anything for real (the in-memory fakes have no such concept) --
    ver el docstring del módulo."""

    def __init__(
        self,
        job_repository: InMemoryJobRepository,
        job_analysis_repository: InMemoryJobAnalysisRepository,
    ) -> None:
        self._job_repository = job_repository
        self._job_analysis_repository = job_analysis_repository

    @contextmanager
    def __call__(self) -> Iterator[JobPipelineRepositories]:
        yield self._job_repository, self._job_analysis_repository


class TestRunDailyPipelineHappyPath:
    def test_posts_end_up_email_generated(self) -> None:
        job_repository = InMemoryJobRepository()
        job_analysis_repository = InMemoryJobAnalysisRepository()
        new_session = _FakeJobPipelineSession(job_repository, job_analysis_repository)
        feed_collector = FakeFeedCollector(posts=[_make_raw_post(content_hash="a" * 64)])
        llm_provider = FakeLLMProvider()
        cv_catalog = FakeCVCatalog()
        cv_matcher = CVMatcher(cv_catalog)

        result = run_daily_pipeline(
            feed_collector=feed_collector,
            new_job_pipeline_session=new_session,
            llm_provider=llm_provider,
            cv_matcher=cv_matcher,
            cv_catalog=cv_catalog,
        )

        assert result.collect_error is None
        assert result.collect_result is not None
        assert result.collect_result.saved == 1

        assert result.analyze_error is None
        assert result.analyze_result is not None
        assert result.analyze_result.relevant == 1

        assert result.select_cv_error is None
        assert result.select_cv_result is not None
        assert result.select_cv_result.cv_selected == 1

        assert result.generate_email_error is None
        assert result.generate_email_result is not None
        assert result.generate_email_result.generated == 1

        job = job_repository.get_by_content_hash("a" * 64)
        assert job is not None
        assert job.status == JobStatus.EMAIL_GENERATED
        job_analysis = job_analysis_repository.get_by_job_id(job.id)
        assert job_analysis is not None
        assert job_analysis.recommended_cv == "java"
        assert job_analysis.subject == "Application for Java role"
        assert job_analysis.generated_email == "Dear team..."

        assert result.duration_seconds >= 0.0

    def test_never_touches_gmail_or_marks_anything_sent(self) -> None:
        # No hay ningún `EmailDraftRepository`/`ApplicationRepository` en la
        # firma de `run_daily_pipeline` -- este test documenta esa
        # restricción explícitamente en vez de dejarla implícita.
        job_repository = InMemoryJobRepository()
        job_analysis_repository = InMemoryJobAnalysisRepository()
        new_session = _FakeJobPipelineSession(job_repository, job_analysis_repository)
        feed_collector = FakeFeedCollector(posts=[_make_raw_post(content_hash="b" * 64)])
        cv_catalog = FakeCVCatalog()

        run_daily_pipeline(
            feed_collector=feed_collector,
            new_job_pipeline_session=new_session,
            llm_provider=FakeLLMProvider(),
            cv_matcher=CVMatcher(cv_catalog),
            cv_catalog=cv_catalog,
        )

        job = job_repository.get_by_content_hash("b" * 64)
        assert job is not None
        # EMAIL_GENERATED es el status final que este pipeline puede
        # producir -- DRAFT_CREATED/SENT solo ocurren vía el flujo manual
        # de revisión (POST /api/jobs/{id}/create-draft), nunca acá.
        assert job.status == JobStatus.EMAIL_GENERATED


class TestRunDailyPipelineHandlesCollectFailureWithoutAbortingLaterStages:
    def test_linkedin_auth_failure_still_runs_later_stages_over_the_existing_backlog(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # Un `Job` ya en CV_SELECTED de una corrida anterior debe seguir
        # avanzando aunque la etapa de collect falle por completo hoy (p.
        # ej. la sesión de LinkedIn expiró).
        job_repository = InMemoryJobRepository()
        job_analysis_repository = InMemoryJobAnalysisRepository()
        backlog_job = _make_cv_selected_job(content_hash="c" * 64)
        job_repository.seed(backlog_job)
        job_analysis_repository.seed(_make_job_analysis_with_cv(job_id=backlog_job.id))
        new_session = _FakeJobPipelineSession(job_repository, job_analysis_repository)
        cv_catalog = FakeCVCatalog()

        with caplog.at_level(logging.WARNING):
            result = run_daily_pipeline(
                feed_collector=FakeFeedCollector(error=LinkedInAuthenticationError("no session")),
                new_job_pipeline_session=new_session,
                llm_provider=FakeLLMProvider(),
                cv_matcher=CVMatcher(cv_catalog),
                cv_catalog=cv_catalog,
            )

        assert result.collect_result is None
        assert result.collect_error == "LinkedInAuthenticationError"

        # Las etapas siguientes corrieron igual, sobre el backlog existente.
        assert result.generate_email_result is not None
        assert result.generate_email_result.generated == 1
        backlog_job_after = job_repository.get_by_id(backlog_job.id)
        assert backlog_job_after is not None
        assert backlog_job_after.status == JobStatus.EMAIL_GENERATED

        assert any(
            "LinkedInAuthenticationError" in record.getMessage() for record in caplog.records
        )

    def test_persist_failure_is_reported_and_does_not_abort_later_stages(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # Regression test for a HIGH finding from `code-reviewer`:
        # `CollectFeedPosts.persist()` propagating `IntegrityError` (a
        # concurrent duplicate `content_hash` race, see
        # `app/presentation/api/routes/scrape.py`) used to escape
        # `_run_collect_stage` uncaught, aborting `run_daily_pipeline`
        # before analyze/select-cv/generate-email ever ran over the
        # existing backlog.
        job_repository = _RaisesOnceThenWorksJobRepository()
        job_analysis_repository = InMemoryJobAnalysisRepository()
        backlog_job = _make_cv_selected_job(content_hash="z" * 64)
        job_repository.seed(backlog_job)
        job_analysis_repository.seed(_make_job_analysis_with_cv(job_id=backlog_job.id))
        new_session = _FakeJobPipelineSession(job_repository, job_analysis_repository)
        cv_catalog = FakeCVCatalog()

        with caplog.at_level(logging.ERROR):
            result = run_daily_pipeline(
                feed_collector=FakeFeedCollector(posts=[_make_raw_post(content_hash="y" * 64)]),
                new_job_pipeline_session=new_session,
                llm_provider=FakeLLMProvider(),
                cv_matcher=CVMatcher(cv_catalog),
                cv_catalog=cv_catalog,
            )

        assert result.collect_result is None
        assert result.collect_error == "IntegrityError"

        # Las etapas siguientes corrieron igual, sobre el backlog existente.
        assert result.generate_email_result is not None
        assert result.generate_email_result.generated == 1
        backlog_job_after = job_repository.get_by_id(backlog_job.id)
        assert backlog_job_after is not None
        assert backlog_job_after.status == JobStatus.EMAIL_GENERATED

        assert any("IntegrityError" in record.getMessage() for record in caplog.records)


class TestRunDailyPipelinePartialFailureDoesNotRevertEarlierStages:
    def test_broken_cv_catalog_does_not_undo_the_already_persisted_analysis(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        job_repository = InMemoryJobRepository()
        job_analysis_repository = InMemoryJobAnalysisRepository()
        new_session = _FakeJobPipelineSession(job_repository, job_analysis_repository)
        feed_collector = FakeFeedCollector(posts=[_make_raw_post(content_hash="d" * 64)])
        llm_provider = FakeLLMProvider()
        broken_cv_catalog = FakeCVCatalog(broken=True)

        with caplog.at_level(logging.ERROR):
            result = run_daily_pipeline(
                feed_collector=feed_collector,
                new_job_pipeline_session=new_session,
                llm_provider=llm_provider,
                cv_matcher=CVMatcher(broken_cv_catalog),
                cv_catalog=broken_cv_catalog,
            )

        # Collect + analyze ya persistieron con éxito...
        assert result.collect_result is not None
        assert result.collect_result.saved == 1
        assert result.analyze_result is not None
        assert result.analyze_result.relevant == 1

        # ...y ese trabajo sigue en pie a pesar de que select-cv reventó por
        # un catálogo de CVs roto.
        job = job_repository.get_by_content_hash("d" * 64)
        assert job is not None
        assert job.status == JobStatus.RELEVANT
        job_analysis = job_analysis_repository.get_by_job_id(job.id)
        assert job_analysis is not None
        assert job_analysis.job_type == "Java"
        assert job_analysis.recommended_cv is None

        # select-cv se reporta como fallido a nivel de etapa...
        assert result.select_cv_result is None
        assert result.select_cv_error == "CVCatalogNotFoundError"

        # ...y generate-email ni siquiera corrió sobre nada (no hay ningún
        # Job en CV_SELECTED todavía), pero la etapa en sí no crasheó.
        assert result.generate_email_result is not None
        assert result.generate_email_result.generated == 0
        assert result.generate_email_error is None

        assert any("CVCatalogNotFoundError" in record.getMessage() for record in caplog.records)

    def test_llm_failure_during_analyze_does_not_revert_the_persisted_collect_stage(self) -> None:
        job_repository = InMemoryJobRepository()
        job_analysis_repository = InMemoryJobAnalysisRepository()
        new_session = _FakeJobPipelineSession(job_repository, job_analysis_repository)
        feed_collector = FakeFeedCollector(posts=[_make_raw_post(content_hash="e" * 64)])

        class _FailingLLMProvider:
            def analyze_job(self, content: str) -> JobAnalysisResult:
                raise LLMProviderError("provider timed out")

            def generate_email(self, context: EmailContext) -> GeneratedEmail:
                raise NotImplementedError("Not used by this test.")

        cv_catalog = FakeCVCatalog()

        result = run_daily_pipeline(
            feed_collector=feed_collector,
            new_job_pipeline_session=new_session,
            llm_provider=_FailingLLMProvider(),
            cv_matcher=CVMatcher(cv_catalog),
            cv_catalog=cv_catalog,
        )

        # El collect stage ya persistió el Job -- sigue ahí, en SCRAPED
        # (AnalyzeJobPost.execute() atrapa el fallo del LLM internamente y
        # no lo marca como analizado, para reintento futuro).
        job = job_repository.get_by_content_hash("e" * 64)
        assert job is not None
        assert job.status == JobStatus.SCRAPED

        assert result.collect_result is not None
        assert result.collect_result.saved == 1
        assert result.analyze_result is not None
        assert result.analyze_result.failed_llm_calls == 1
        assert result.analyze_error is None


class TestRunDailyPipelineNeverCreatesGmailDependency:
    def test_signature_has_no_gmail_or_application_dependency(self) -> None:
        import inspect

        signature = inspect.signature(run_daily_pipeline)
        assert "email_draft_repository" not in signature.parameters
        assert "application_repository" not in signature.parameters


class _ListByStatusIgnoresActualStatusJobRepository(InMemoryJobRepository):
    """Simulates the race condition documented in the module docstring
    (sección "Aislamiento de errores por etapa"): a concurrent actor (e.g.
    the manual dashboard) already transitioned `misbehaving_job` away from
    `target_status` between `list_by_status` listing it for this stage and
    the stage actually acting on it. `list_by_status(target_status)`
    returns `misbehaving_job` regardless of its real (already-moved-on)
    status; every other status is filtered normally, so later stages in
    the same `run_daily_pipeline` call still see it wherever it actually
    is."""

    def __init__(self, *, target_status: JobStatus, misbehaving_job: Job) -> None:
        super().__init__()
        self.seed(misbehaving_job)
        self._target_status = target_status
        self._misbehaving_job = misbehaving_job

    def list_by_status(self, status: JobStatus, *, limit: int = 50, offset: int = 0) -> list[Job]:
        if status == self._target_status:
            return [self._misbehaving_job]
        return super().list_by_status(status, limit=limit, offset=offset)


class TestRunDailyPipelineStageLevelInvalidStateTransition:
    """`_STAGE_LEVEL_ERRORS` (`InvalidStateTransitionError`,
    `CVInfrastructureError`) atrapa, a nivel de etapa completa, fallos que
    `AnalyzeJobPost`/`GenerateApplicationEmail` no atrapan internamente por
    job dentro de su propio loop de batch -- ver el docstring del módulo,
    sección "Aislamiento de errores por etapa". El caso de
    `CVInfrastructureError` en `select-cv` ya está cubierto por
    `TestRunDailyPipelinePartialFailureDoesNotRevertEarlierStages
    .test_broken_cv_catalog_does_not_undo_the_already_persisted_analysis`;
    estos dos tests cubren la misma protección para las etapas de analyze y
    generate-email, vía `InvalidStateTransitionError` -- el único de los dos
    errores que esas dos etapas pueden propagar sin atraparlo (`GenerateApplicationEmail.execute()`
    sí atrapa `CVInfrastructureError` internamente, ver su docstring)."""

    def test_analyze_stage_failure_is_reported_without_aborting_the_run(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        already_relevant_job = Job.create(
            source="linkedin",
            author="Jane Recruiter",
            content="We are hiring a Senior Java Engineer, remote, apply now!",
            content_hash="race-analyze" + "0" * 52,
            email=None,
            url="https://www.linkedin.com/feed/update/race-analyze/",
            published_at=_NOW,
            scraped_at=_NOW,
            created_at=_NOW,
        )
        already_relevant_job.mark_analyzed()
        already_relevant_job.mark_relevant()

        job_repository = _ListByStatusIgnoresActualStatusJobRepository(
            target_status=JobStatus.SCRAPED, misbehaving_job=already_relevant_job
        )
        job_analysis_repository = InMemoryJobAnalysisRepository()
        new_session = _FakeJobPipelineSession(job_repository, job_analysis_repository)
        cv_catalog = FakeCVCatalog()

        with caplog.at_level(logging.ERROR):
            result = run_daily_pipeline(
                feed_collector=FakeFeedCollector(posts=[]),
                new_job_pipeline_session=new_session,
                llm_provider=FakeLLMProvider(),
                cv_matcher=CVMatcher(cv_catalog),
                cv_catalog=cv_catalog,
            )

        assert result.analyze_result is None
        assert result.analyze_error == "InvalidStateTransitionError"
        assert any(
            "InvalidStateTransitionError" in record.getMessage() for record in caplog.records
        )
        # El job -- ya en RELEVANT antes de esta corrida -- no se pierde:
        # select-cv (que lee `list_by_status(RELEVANT)` normalmente, vía
        # `super()`) lo procesa igual en esta misma corrida.
        assert result.select_cv_result is not None
        assert result.select_cv_result.cv_selected == 1

    def test_generate_email_stage_failure_is_reported_without_aborting_the_run(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        already_email_generated_job = Job.create(
            source="linkedin",
            author="Jane Recruiter",
            content="We are hiring a Senior Java Engineer, remote, apply now!",
            content_hash="race-generate" + "0" * 51,
            email=None,
            url="https://www.linkedin.com/feed/update/race-generate/",
            published_at=_NOW,
            scraped_at=_NOW,
            created_at=_NOW,
        )
        already_email_generated_job.mark_analyzed()
        already_email_generated_job.mark_relevant()
        already_email_generated_job.mark_cv_selected()
        already_email_generated_job.mark_email_generated()

        job_repository = _ListByStatusIgnoresActualStatusJobRepository(
            target_status=JobStatus.CV_SELECTED, misbehaving_job=already_email_generated_job
        )
        job_analysis_repository = InMemoryJobAnalysisRepository()
        job_analysis_repository.seed(
            _make_job_analysis_with_cv(job_id=already_email_generated_job.id)
        )
        new_session = _FakeJobPipelineSession(job_repository, job_analysis_repository)
        cv_catalog = FakeCVCatalog()

        with caplog.at_level(logging.ERROR):
            result = run_daily_pipeline(
                feed_collector=FakeFeedCollector(posts=[]),
                new_job_pipeline_session=new_session,
                llm_provider=FakeLLMProvider(),
                cv_matcher=CVMatcher(cv_catalog),
                cv_catalog=cv_catalog,
            )

        assert result.generate_email_result is None
        assert result.generate_email_error == "InvalidStateTransitionError"
        assert any(
            "InvalidStateTransitionError" in record.getMessage() for record in caplog.records
        )
