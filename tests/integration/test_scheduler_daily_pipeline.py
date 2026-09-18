"""Integration test for `run_daily_pipeline`
(`app/presentation/scheduler/jobs.py`) against a real PostgreSQL database.

Ejercita `run_daily_pipeline` con dependencias fake para LinkedIn/LLM (nunca
Playwright ni Anthropic/OpenAI reales, ver `docs/agents/AGENTS.md` sección
8) pero con la fábrica de sesión real del scheduler
(`_sql_job_pipeline_session`, respaldada por `SQLAlchemyJobRepository`/
`SQLAlchemyJobAnalysisRepository` sobre `session_scope()`), confirmando que
un `Job` recién scrapeado termina en `EMAIL_GENERATED` en la base real, tras
una única corrida.

Mismo patrón que `tests/integration/test_jobs_analyze_checkpoint.py`: se
salta limpiamente con `pytest.skip` si no hay PostgreSQL disponible.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.application.cv.cv_profile import CVProfile
from app.application.cv.matcher import CVMatcher
from app.application.dto.email_context import EmailContext
from app.application.dto.generated_email import GeneratedEmail
from app.application.dto.job_analysis_result import JobAnalysisResult
from app.application.dto.raw_feed_post import RawFeedPost
from app.domain.value_objects.job_status import JobStatus
from app.infrastructure.database.repositories.sqlalchemy_job_analysis_repository import (
    SQLAlchemyJobAnalysisRepository,
)
from app.infrastructure.database.repositories.sqlalchemy_job_repository import (
    SQLAlchemyJobRepository,
)
from app.infrastructure.database.session import get_engine, get_session_factory
from app.presentation.scheduler.jobs import _sql_job_pipeline_session, run_daily_pipeline

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 9, 17, 8, 0, tzinfo=UTC)
_CONTENT_HASH = ("scheduler-integration-test-" + "f" * 64)[:64]


class _StubFeedCollector:
    """Returns a single fixed post -- never real Playwright/LinkedIn."""

    def __init__(self, posts: list[RawFeedPost]) -> None:
        self._posts = posts

    def collect(self) -> list[RawFeedPost]:
        return list(self._posts)


class _StubLLMProvider:
    """Always reports the post as a relevant Java job and always returns a
    fixed generated email -- never a real Anthropic/OpenAI call."""

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


class _StubCVCatalog:
    """A single `java` CV, matching `_StubLLMProvider`'s detected skills --
    never touches the real `config/cvs.yaml`."""

    def list_cvs(self) -> list[CVProfile]:
        return [
            CVProfile(
                id="java",
                file="cvs/java/william-java.pdf",
                skills=("Java", "Spring Boot", "Kafka"),
                summary="Java backend engineer.",
            )
        ]

    def get_summary(self, cv_id: str) -> str:
        return "Java backend engineer."


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


def _delete_job_by_content_hash(session: Session, content_hash: str) -> None:
    job_repository = SQLAlchemyJobRepository(session)
    job = job_repository.get_by_content_hash(content_hash)
    if job is None:
        return
    from app.infrastructure.database.sqlalchemy_models import JobModel

    model = session.get(JobModel, job.id.value)
    if model is not None:
        session.delete(model)
        session.commit()


class TestRunDailyPipelineAgainstRealPostgres:
    def test_a_freshly_scraped_post_ends_up_email_generated(self, db_session: Session) -> None:
        raw_post = RawFeedPost(
            author="Jane Recruiter",
            content="We are hiring a Senior Java Engineer, remote, apply now!",
            content_hash=_CONTENT_HASH,
            url="https://www.linkedin.com/feed/update/scheduler-integration-test/",
            published_at=_NOW,
        )

        try:
            result = run_daily_pipeline(
                feed_collector=_StubFeedCollector(posts=[raw_post]),
                new_job_pipeline_session=_sql_job_pipeline_session,
                llm_provider=_StubLLMProvider(),
                cv_matcher=CVMatcher(_StubCVCatalog()),
                cv_catalog=_StubCVCatalog(),
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

            # Fresh repositories/session (each stage opened and committed
            # its own real Session, already closed by now) to confirm what
            # actually landed in PostgreSQL.
            verification_job_repository = SQLAlchemyJobRepository(db_session)
            verification_analysis_repository = SQLAlchemyJobAnalysisRepository(db_session)

            persisted_job = verification_job_repository.get_by_content_hash(_CONTENT_HASH)
            assert persisted_job is not None
            assert persisted_job.status == JobStatus.EMAIL_GENERATED

            persisted_analysis = verification_analysis_repository.get_by_job_id(persisted_job.id)
            assert persisted_analysis is not None
            assert persisted_analysis.job_type == "Java"
            assert persisted_analysis.recommended_cv == "java"
            assert persisted_analysis.subject == "Application for Java role"
            assert persisted_analysis.generated_email == "Dear team..."
        finally:
            _delete_job_by_content_hash(db_session, _CONTENT_HASH)
