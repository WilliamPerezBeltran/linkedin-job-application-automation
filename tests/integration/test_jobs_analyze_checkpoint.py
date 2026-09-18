"""Integration test for the intermediate commit checkpoint in
`POST /api/jobs/{id}/analyze` (`app/presentation/api/routes/jobs.py`).

Regression test for a HIGH finding from `code-reviewer` on Fase 6: without
an explicit `session.commit()` between `AnalyzeJobPost.execute_one` and the
chained `SelectBestCV.execute_one`, a `CVInfrastructureError` raised by the
CV Matcher step made `session_scope()` roll back the **entire** request
`Session` -- discarding the `JobAnalysis` that `AnalyzeJobPost` had already
persisted successfully (and the real, paid-for LLM call behind it), and
reverting the `Job` back to `SCRAPED` as if nothing had happened.

This behavior is invisible to unit tests built on in-memory fakes (see
`tests/unit/presentation/api/routes/test_jobs.py`,
`TestAnalyzeJob.test_commits_the_checkpoint_before_chaining_cv_selection`,
which only asserts that `session.commit()` is *called*): a fake repository
has no notion of a real transaction to roll back, so it cannot expose a bug
that only exists at the real-`Session`/PostgreSQL boundary. This test
exercises the actual endpoint against a real PostgreSQL `Session` (only the
LLM provider and the CV Matcher's catalog are faked, via
`app.dependency_overrides` -- never a real Anthropic call, never a real
`config/cvs.yaml`), and asserts the `Job`/`JobAnalysis` end up in the state
the checkpoint commit is meant to guarantee.

Mismo patrón que el resto de `tests/integration/`: se salta limpiamente con
`pytest.skip` si no hay PostgreSQL disponible.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.application.cv.cv_profile import CVProfile
from app.application.cv.matcher import CVMatcher
from app.application.dto.email_context import EmailContext
from app.application.dto.generated_email import GeneratedEmail
from app.application.dto.job_analysis_result import JobAnalysisResult
from app.domain.entities.job import Job
from app.domain.value_objects.job_id import JobId
from app.domain.value_objects.job_status import JobStatus
from app.infrastructure.cv.exceptions import CVCatalogNotFoundError
from app.infrastructure.database.repositories.sqlalchemy_job_analysis_repository import (
    SQLAlchemyJobAnalysisRepository,
)
from app.infrastructure.database.repositories.sqlalchemy_job_repository import (
    SQLAlchemyJobRepository,
)
from app.infrastructure.database.session import get_engine, get_session_factory
from app.main import app
from app.presentation.api.dependencies import get_cv_matcher, get_llm_provider

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)

client = TestClient(app)


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


@pytest.fixture(autouse=True)
def _clear_dependency_overrides() -> Iterator[None]:
    yield
    app.dependency_overrides.clear()


def _delete_job(session: Session, job_id: JobId) -> None:
    from app.infrastructure.database.sqlalchemy_models import JobModel

    model = session.get(JobModel, job_id.value)
    if model is not None:
        session.delete(model)
        session.commit()


class _StubLLMProvider:
    """Always reports the post as a relevant Java job -- never a real call."""

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
        raise NotImplementedError("Not used by this test.")


class _FailingCVCatalog:
    """Simulates a broken `config/cvs.yaml` -- `CVMatcher.match()` propagates
    this straight through, exercising the endpoint's `CVInfrastructureError`
    -> 500 branch during the chained `SelectBestCV` step."""

    def list_cvs(self) -> list[CVProfile]:
        raise CVCatalogNotFoundError("config/cvs.yaml")

    def get_summary(self, cv_id: str) -> str:
        raise CVCatalogNotFoundError("config/cvs.yaml")


class TestAnalyzeCheckpointSurvivesAFailedChainedCVMatch:
    def test_job_analysis_is_not_rolled_back_when_cv_matching_fails(
        self, db_session: Session
    ) -> None:
        job_repository = SQLAlchemyJobRepository(db_session)
        job = Job.create(
            source="linkedin",
            author="Jane Recruiter",
            content="We are hiring a Senior Java Engineer, remote, apply now!",
            content_hash=f"integration-test-checkpoint-{JobId.new()}",
            email=None,
            url="https://www.linkedin.com/feed/update/checkpoint/",
            published_at=_NOW,
            scraped_at=_NOW,
            created_at=_NOW,
        )

        try:
            job_repository.save(job)
            db_session.commit()

            app.dependency_overrides[get_llm_provider] = lambda: _StubLLMProvider()
            app.dependency_overrides[get_cv_matcher] = lambda: CVMatcher(_FailingCVCatalog())

            response = client.post(f"/api/jobs/{job.id}/analyze")

            assert response.status_code == 500

            # Fresh repositories/session (the endpoint's own Session was
            # already committed and closed by FastAPI's dependency cleanup)
            # to confirm what actually landed in PostgreSQL.
            verification_job_repository = SQLAlchemyJobRepository(db_session)
            verification_analysis_repository = SQLAlchemyJobAnalysisRepository(db_session)

            persisted_job = verification_job_repository.get_by_id(job.id)
            assert persisted_job is not None
            # Before the checkpoint-commit fix, this was JobStatus.SCRAPED --
            # the chained CVInfrastructureError rolled back the whole
            # request Session, discarding AnalyzeJobPost's already-successful
            # work.
            assert persisted_job.status == JobStatus.RELEVANT

            persisted_analysis = verification_analysis_repository.get_by_job_id(job.id)
            assert persisted_analysis is not None
            assert persisted_analysis.job_type == "Java"
            assert persisted_analysis.recommended_cv is None
        finally:
            _delete_job(db_session, job.id)
