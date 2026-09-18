"""Integration test for the `IntegrityError` recovery in
`POST /api/jobs/{id}/create-draft` (`app/presentation/api/routes/jobs.py`).

Regression test for a MEDIUM finding from `code-reviewer` on the Fases 7-10
closing review: two genuinely concurrent requests for the same `Job` can
both pass `CreateGmailDraft.execute_one`'s idempotency check
(`application_repository.get_by_job_id(job.id) is None`) before either
commits, both create a real Gmail draft, and the second
`application_repository.save()` loses the race against the real
`uq_applications_job_id` constraint (`app/infrastructure/database/
sqlalchemy_models.py::ApplicationModel`). The router recovers with an
explicit `session.rollback()` + re-query -- see `create_draft`'s docstring
in `app/presentation/api/routes/jobs.py` for the full reasoning.

This behavior is invisible to unit tests built on in-memory fakes (see
`tests/unit/presentation/api/routes/test_jobs.py`, `TestCreateDraft`'s
`test_returns_the_winning_application_when_a_concurrent_request_wins_the_race`/
`test_returns_409_when_the_race_is_still_unresolved_after_rollback`, which
simulate `IntegrityError` via `RacingApplicationRepository` and count calls
on a `FakeSession.rollback()` that is just a counter): a fake repository has
no notion of a real transaction, so it cannot expose whether
`Session.rollback()` actually leaves a real SQLAlchemy `Session` usable for
the query that follows, or whether the real `UniqueConstraint` genuinely
fires `IntegrityError` under a concurrent flush. Same pattern already
established by `tests/integration/test_jobs_analyze_checkpoint.py` for the
sibling HIGH finding on this same router (the intermediate commit
checkpoint) -- mirrored here for this MEDIUM finding.

Genuine concurrency, not a stub: a background thread opens its own
`Session`/PostgreSQL connection, inserts (and `flush()`es, but does not
commit) an `Application` row for the same `job_id` the main thread's HTTP
request targets. PostgreSQL blocks the main request's conflicting `INSERT`
on the unique index until the background thread commits, at which point the
main request's own `flush()` (inside `SQLAlchemyApplicationRepository.save`)
raises a real `IntegrityError` -- exactly the production race, not a
simulation of its effect.

Se salta limpiamente con `pytest.skip` si no hay PostgreSQL disponible
(mismo patrón que el resto de `tests/integration/`).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.application.cv.cv_profile import CVProfile
from app.domain.entities.application import Application
from app.domain.entities.job import Job
from app.domain.entities.job_analysis import JobAnalysis
from app.domain.value_objects.email_address import EmailAddress
from app.domain.value_objects.job_id import JobId
from app.infrastructure.database.repositories.sqlalchemy_application_repository import (
    SQLAlchemyApplicationRepository,
)
from app.infrastructure.database.repositories.sqlalchemy_job_analysis_repository import (
    SQLAlchemyJobAnalysisRepository,
)
from app.infrastructure.database.repositories.sqlalchemy_job_repository import (
    SQLAlchemyJobRepository,
)
from app.infrastructure.database.session import get_engine, get_session_factory
from app.main import app
from app.presentation.api.dependencies import get_cv_catalog, get_email_draft_repository

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


class _StubCVCatalog:
    """Always resolves the `python` CV -- never reads `config/cvs.yaml`."""

    def list_cvs(self) -> list[CVProfile]:
        return [
            CVProfile(
                id="python",
                file="cvs/python/william-python.pdf",
                skills=("Python", "FastAPI"),
                summary="Python backend engineer.",
            )
        ]

    def get_summary(self, cv_id: str) -> str:
        return "Python backend engineer."


class _StubEmailDraftRepository:
    """Always reports a successful Gmail draft creation -- never calls the
    real Gmail API. The losing request in this race still "creates" a draft
    here (mirroring production: `CreateGmailDraft.execute_one` calls Gmail
    *before* attempting to persist the `Application`), it just never gets
    to keep it."""

    def create_draft(self, *, to: EmailAddress, subject: str, body: str, cv_path: str) -> str:
        return "losing-request-draft-id"


class TestCreateDraftSurvivesAConcurrentApplicationInsert:
    def test_returns_the_already_committed_winning_application(
        self, db_session: Session, caplog: pytest.LogCaptureFixture
    ) -> None:
        job = Job.create(
            source="linkedin",
            author="Jane Recruiter",
            content="We are hiring a Senior Python Engineer, remote, apply now!",
            content_hash=f"integration-test-draft-race-{JobId.new()}",
            email=EmailAddress("recruiter@example.com"),
            url="https://www.linkedin.com/feed/update/create-draft-race/",
            published_at=_NOW,
            scraped_at=_NOW,
            created_at=_NOW,
        )
        job.mark_analyzed()
        job.mark_relevant()
        job.mark_cv_selected()
        job.mark_email_generated()

        job_analysis = JobAnalysis(
            job_id=job.id,
            job_type="Python",
            seniority="Senior",
            skills=("Python", "FastAPI"),
            languages=("Python",),
            frameworks=("FastAPI",),
            cloud=(),
            ai_related=False,
        )
        job_analysis.record_cv_recommendation(recommended_cv="python", match_score=1.0)
        job_analysis.record_generated_email(
            subject="Application for Python role", body="Dear team..."
        )

        winning_application = Application.create(
            job_id=job.id,
            email=EmailAddress("recruiter@example.com"),
            subject="Application for Python role",
            body="Dear team...",
            cv_path="cvs/python/william-python.pdf",
            gmail_draft_id="winning-request-draft-id",
        )

        background_session = get_session_factory()()
        flushed_event = threading.Event()

        def _insert_winning_application_and_delay_commit() -> None:
            background_application_repository = SQLAlchemyApplicationRepository(
                background_session
            )
            background_application_repository.save(winning_application)
            # Now committed in the unique index but not yet visible to other
            # transactions -- holds the lock a conflicting concurrent INSERT
            # would need, exactly like a real concurrent request that got a
            # few milliseconds ahead. Signal the main thread *after* the
            # flush, then hold this transaction open briefly before
            # committing, so the main thread's own idempotency SELECT (fired
            # right after the signal) reliably runs before this commits.
            flushed_event.set()
            time.sleep(0.3)
            background_session.commit()

        background_thread = threading.Thread(
            target=_insert_winning_application_and_delay_commit
        )

        try:
            job_repository = SQLAlchemyJobRepository(db_session)
            job_analysis_repository = SQLAlchemyJobAnalysisRepository(db_session)
            job_repository.save(job)
            job_analysis_repository.save(job_analysis)
            db_session.commit()

            app.dependency_overrides[get_cv_catalog] = lambda: _StubCVCatalog()
            app.dependency_overrides[get_email_draft_repository] = (
                lambda: _StubEmailDraftRepository()
            )

            background_thread.start()
            assert flushed_event.wait(timeout=5), "background flush never signaled readiness"

            # Blocks on PostgreSQL's unique-index lock until the background
            # thread commits (see its `time.sleep(0.3)`), then this
            # request's own `flush()` raises a real `IntegrityError` because
            # the row now genuinely exists -- exercising
            # `create_draft`'s `except IntegrityError` branch against a real
            # `Session`/constraint, not a fake.
            with caplog.at_level("WARNING"):
                response = client.post(f"/api/jobs/{job.id}/create-draft")
            background_thread.join(timeout=5)
            background_session.close()

            # Guards against this test silently degrading into only
            # exercising the ordinary idempotency path (same observable
            # 200 response) if the background thread's commit ever landed
            # *before* this request's own idempotency SELECT under adverse
            # timing -- confirms `create_draft`'s `except IntegrityError`
            # branch (`jobs.create_draft.application_race`, see
            # `app/presentation/api/routes/jobs.py`) is the one that
            # actually ran.
            assert any(
                "jobs.create_draft.application_race" in record.getMessage()
                and str(job.id) in record.getMessage()
                for record in caplog.records
            )

            assert response.status_code == 200
            payload = response.json()
            # The router recovered by returning the *other* request's
            # already-committed Application, not its own (this request's own
            # Gmail draft, "losing-request-draft-id", was created for
            # nothing -- the orphaned-draft risk `CreateGmailDraft.execute_one`
            # now logs, see its module docstring point 7 -- but never got
            # persisted here).
            assert payload["gmail_draft_id"] == "winning-request-draft-id"
            assert payload["job_id"] == str(job.id)

            verification_application_repository = SQLAlchemyApplicationRepository(db_session)
            persisted_application = verification_application_repository.get_by_job_id(job.id)
            assert persisted_application is not None
            assert persisted_application.gmail_draft_id == "winning-request-draft-id"
        finally:
            # Cleanup only, after every assertion above already ran --
            # deleting the Job here cascades (`ondelete="CASCADE"`) to the
            # Application row this test just verified, so it must not run
            # any earlier.
            if background_thread.is_alive():
                background_thread.join(timeout=5)
            _delete_job(db_session, job.id)
