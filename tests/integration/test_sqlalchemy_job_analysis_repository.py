"""Integration test for `SQLAlchemyJobAnalysisRepository` against a real
PostgreSQL.

Mismo patrón que `test_sqlalchemy_job_repository.py`: salta limpiamente con
`pytest.skip` si no hay conexión disponible, y testea el comportamiento
prometido por la interfaz `JobAnalysisRepository` (structural typing) — no
SQLAlchemy directamente.

`job_analysis.job_id` es FK a `jobs.id` (ver
`app/infrastructure/database/sqlalchemy_models.py`), así que cada test
primero persiste un `Job` (vía `SQLAlchemyJobRepository`) antes de guardar
su `JobAnalysis` asociado.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.domain.entities.job import Job
from app.domain.entities.job_analysis import JobAnalysis
from app.domain.value_objects.email_address import EmailAddress
from app.domain.value_objects.job_id import JobId
from app.infrastructure.database.repositories.sqlalchemy_job_analysis_repository import (
    SQLAlchemyJobAnalysisRepository,
)
from app.infrastructure.database.repositories.sqlalchemy_job_repository import (
    SQLAlchemyJobRepository,
)
from app.infrastructure.database.session import get_engine, get_session_factory

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)


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


def _make_job(**overrides: object) -> Job:
    defaults: dict[str, object] = {
        "source": "linkedin_feed",
        "author": "Jane Recruiter",
        "content": "We are hiring a Senior Python Engineer...",
        "content_hash": f"integration-test-analysis-{JobId.new()}",
        "email": EmailAddress("jobs@example.com"),
        "url": "https://www.linkedin.com/feed/update/urn:li:activity:123/",
        "published_at": _NOW,
        "scraped_at": _NOW,
        "created_at": _NOW,
    }
    defaults.update(overrides)
    return Job.create(**defaults)  # type: ignore[arg-type]


def _make_job_analysis(job_id: JobId, **overrides: object) -> JobAnalysis:
    defaults: dict[str, object] = {
        "job_id": job_id,
        "job_type": "AI/ML",
        "seniority": "Senior",
        "skills": ["Python", "PyTorch"],
        "languages": ["Python"],
        "frameworks": ["FastAPI"],
        "cloud": ["AWS"],
        "ai_related": True,
    }
    defaults.update(overrides)
    return JobAnalysis(**defaults)  # type: ignore[arg-type]


def _delete_job(session: Session, job_id: JobId) -> None:
    """Cleans up a test job row directly (cascades to `job_analysis` via
    `ondelete=CASCADE`), keeping the test repeatable across runs."""
    from app.infrastructure.database.sqlalchemy_models import JobModel

    model = session.get(JobModel, job_id.value)
    if model is not None:
        session.delete(model)
        session.commit()


class TestSQLAlchemyJobAnalysisRepositoryIntegration:
    def test_save_then_get_by_job_id_returns_an_equivalent_job_analysis(
        self, db_session: Session
    ) -> None:
        job_repository = SQLAlchemyJobRepository(db_session)
        analysis_repository = SQLAlchemyJobAnalysisRepository(db_session)
        job = _make_job()
        analysis = _make_job_analysis(job.id)

        try:
            job_repository.save(job)
            analysis_repository.save(analysis)
            db_session.commit()

            fetched = analysis_repository.get_by_job_id(job.id)

            assert fetched is not None
            assert fetched.job_id == analysis.job_id
            assert fetched.job_type == analysis.job_type
            assert fetched.seniority == analysis.seniority
            assert fetched.skills == analysis.skills
            assert fetched.languages == analysis.languages
            assert fetched.frameworks == analysis.frameworks
            assert fetched.cloud == analysis.cloud
            assert fetched.ai_related == analysis.ai_related
            assert fetched.match_score == analysis.match_score
            assert fetched.recommended_cv == analysis.recommended_cv
            assert fetched.generated_email == analysis.generated_email
            assert fetched.subject == analysis.subject
        finally:
            _delete_job(db_session, job.id)

    def test_save_upserts_cv_recommendation_and_generated_email(self, db_session: Session) -> None:
        """`save()` is an upsert keyed by `job_id`: re-saving an
        already-persisted analysis (e.g. after `record_cv_recommendation`
        and `record_generated_email`) updates the same row instead of
        inserting a duplicate."""
        job_repository = SQLAlchemyJobRepository(db_session)
        analysis_repository = SQLAlchemyJobAnalysisRepository(db_session)
        job = _make_job()
        analysis = _make_job_analysis(job.id)

        try:
            job_repository.save(job)
            analysis_repository.save(analysis)
            db_session.commit()

            analysis.record_cv_recommendation(
                recommended_cv="cvs/ai/william-ai.pdf", match_score=0.87
            )
            analysis.record_generated_email(
                subject="Application for Senior AI/ML Engineer",
                body="Dear Hiring Manager, ...",
            )
            analysis_repository.save(analysis)
            db_session.commit()

            fetched = analysis_repository.get_by_job_id(job.id)
            assert fetched is not None
            assert fetched.recommended_cv == "cvs/ai/william-ai.pdf"
            assert fetched.match_score == 0.87
            assert fetched.subject == "Application for Senior AI/ML Engineer"
            assert fetched.generated_email == "Dear Hiring Manager, ..."

            from app.infrastructure.database.sqlalchemy_models import JobAnalysisModel

            rows = (
                db_session.query(JobAnalysisModel)
                .filter(JobAnalysisModel.job_id == job.id.value)
                .count()
            )
            assert rows == 1
        finally:
            _delete_job(db_session, job.id)

    def test_get_by_job_id_returns_none_when_missing(self, db_session: Session) -> None:
        analysis_repository = SQLAlchemyJobAnalysisRepository(db_session)

        assert analysis_repository.get_by_job_id(JobId.new()) is None
