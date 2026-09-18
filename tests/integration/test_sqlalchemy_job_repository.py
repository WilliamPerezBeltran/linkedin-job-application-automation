"""Integration test for `SQLAlchemyJobRepository` against a real PostgreSQL.

Cumple el "Done cuando" de ROADMAP.md Fase 1: inserta un `Job` a través del
repositorio concreto y lo lee de vuelta **a través de la interfaz
`JobRepository`** (structural typing — no se testea SQLAlchemy directamente,
solo el comportamiento que la interfaz promete).

Requiere una base de datos real corriendo (ver `docker-compose.yml`,
servicio `postgres`: `docker compose up -d postgres`) con las migraciones
de Alembic aplicadas (`alembic upgrade head`). Si no hay conexión
disponible, el módulo completo se salta limpiamente con
`pytest.skip(..., allow_module_level=True)` en vez de fallar con un
traceback críptico de conexión rechazada — así `pytest` sigue siendo
utilizable en una máquina sin Postgres levantado (p. ej. corriendo solo los
tests `unit`).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.domain.entities.job import Job
from app.domain.value_objects.email_address import EmailAddress
from app.domain.value_objects.job_id import JobId
from app.domain.value_objects.job_status import JobStatus
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
        "content_hash": f"integration-test-{JobId.new()}",
        "email": EmailAddress("jobs@example.com"),
        "url": "https://www.linkedin.com/feed/update/urn:li:activity:123/",
        "published_at": _NOW,
        "scraped_at": _NOW,
        "created_at": _NOW,
    }
    defaults.update(overrides)
    return Job.create(**defaults)  # type: ignore[arg-type]


def _delete_job(session: Session, job_id: JobId) -> None:
    """Cleans up a test row directly, keeping the test repeatable across runs."""
    from app.infrastructure.database.sqlalchemy_models import JobModel

    model = session.get(JobModel, job_id.value)
    if model is not None:
        session.delete(model)
        session.commit()


class TestSQLAlchemyJobRepositoryIntegration:
    def test_save_then_get_by_id_returns_an_equivalent_job(self, db_session: Session) -> None:
        repository = SQLAlchemyJobRepository(db_session)
        job = _make_job()

        try:
            repository.save(job)
            db_session.commit()

            fetched = repository.get_by_id(job.id)

            assert fetched is not None
            assert fetched.id == job.id
            assert fetched.source == job.source
            assert fetched.author == job.author
            assert fetched.content == job.content
            assert fetched.content_hash == job.content_hash
            assert fetched.email == job.email
            assert fetched.url == job.url
            assert fetched.published_at == job.published_at
            assert fetched.scraped_at == job.scraped_at
            assert fetched.status == job.status
            assert fetched.created_at == job.created_at
        finally:
            _delete_job(db_session, job.id)

    def test_save_then_get_by_content_hash_returns_the_same_job(self, db_session: Session) -> None:
        repository = SQLAlchemyJobRepository(db_session)
        job = _make_job()

        try:
            repository.save(job)
            db_session.commit()

            fetched = repository.get_by_content_hash(job.content_hash)

            assert fetched is not None
            assert fetched.id == job.id
            assert fetched.content_hash == job.content_hash
        finally:
            _delete_job(db_session, job.id)

    def test_get_by_id_returns_none_when_missing(self, db_session: Session) -> None:
        repository = SQLAlchemyJobRepository(db_session)

        assert repository.get_by_id(JobId.new()) is None

    def test_get_by_content_hash_returns_none_when_missing(self, db_session: Session) -> None:
        repository = SQLAlchemyJobRepository(db_session)

        assert repository.get_by_content_hash("no-such-hash") is None

    def test_save_is_idempotent_and_persists_status_transitions(self, db_session: Session) -> None:
        """`save()` upserts by `job.id`: re-saving an already-persisted job
        (e.g. after a `mark_*` transition) updates the same row instead of
        inserting a duplicate."""
        repository = SQLAlchemyJobRepository(db_session)
        job = _make_job()

        try:
            repository.save(job)
            db_session.commit()

            job.mark_analyzed()
            repository.save(job)
            db_session.commit()

            fetched = repository.get_by_id(job.id)
            assert fetched is not None
            assert fetched.status == JobStatus.ANALYZED

            from app.infrastructure.database.sqlalchemy_models import JobModel

            rows = db_session.query(JobModel).filter(JobModel.id == job.id.value).count()
            assert rows == 1
        finally:
            _delete_job(db_session, job.id)

    def test_content_hash_unique_constraint_prevents_duplicate_posts(
        self, db_session: Session
    ) -> None:
        """Dedup guarantee from `CLAUDE.md`: two different jobs sharing the
        same `content_hash` cannot both be persisted."""
        repository = SQLAlchemyJobRepository(db_session)
        shared_hash = f"integration-test-dup-{JobId.new()}"
        first = _make_job(content_hash=shared_hash)
        second = _make_job(content_hash=shared_hash)

        try:
            repository.save(first)
            db_session.commit()

            with pytest.raises(Exception):  # noqa: B017 - IntegrityError from psycopg2/SQLAlchemy
                # `save()` calls `flush()`, so the unique-constraint violation
                # surfaces here, not at a later `commit()`.
                repository.save(second)
            db_session.rollback()
        finally:
            _delete_job(db_session, first.id)

    def test_list_by_status_returns_only_jobs_in_that_status_oldest_first(
        self, db_session: Session
    ) -> None:
        """ADR-004 sección 1: `list_by_status` filtra por status y ordena
        `created_at` ascendente (FIFO), sin devolver jobs de otros status."""
        repository = SQLAlchemyJobRepository(db_session)
        older = _make_job(created_at=datetime(2026, 9, 10, 12, 0, tzinfo=UTC))
        newer = _make_job(created_at=datetime(2026, 9, 12, 12, 0, tzinfo=UTC))
        other_status = _make_job(created_at=datetime(2026, 9, 11, 12, 0, tzinfo=UTC))
        other_status.mark_analyzed()

        try:
            repository.save(older)
            repository.save(newer)
            repository.save(other_status)
            db_session.commit()

            fetched = repository.list_by_status(JobStatus.SCRAPED)

            fetched_ids = [job.id for job in fetched]
            assert older.id in fetched_ids
            assert newer.id in fetched_ids
            assert other_status.id not in fetched_ids
            assert fetched_ids.index(older.id) < fetched_ids.index(newer.id)
            assert all(job.status == JobStatus.SCRAPED for job in fetched)
        finally:
            _delete_job(db_session, older.id)
            _delete_job(db_session, newer.id)
            _delete_job(db_session, other_status.id)

    def test_list_by_status_respects_limit_and_offset(self, db_session: Session) -> None:
        """`list_by_status` orders globally by `created_at` ascending (no
        filter beyond `status`), so this test does *not* assume the table is
        otherwise empty of `SCRAPED` rows (it might not be, e.g. if another
        process/test left stray rows). Instead it locates where our own jobs
        land in the full ordering and asserts pagination consistency
        relative to that — content and order, not an exact count/partition
        of the whole table."""
        repository = SQLAlchemyJobRepository(db_session)
        jobs = [
            _make_job(created_at=datetime(2026, 9, 13, hour, 0, tzinfo=UTC)) for hour in range(3)
        ]

        try:
            for job in jobs:
                repository.save(job)
            db_session.commit()

            full_ordering = repository.list_by_status(JobStatus.SCRAPED, limit=1_000_000, offset=0)
            full_ids = [job.id for job in full_ordering]
            start = full_ids.index(jobs[0].id)

            first_page = repository.list_by_status(JobStatus.SCRAPED, limit=2, offset=start)
            second_page = repository.list_by_status(JobStatus.SCRAPED, limit=2, offset=start + 2)

            assert [job.id for job in first_page] == full_ids[start : start + 2]
            assert [job.id for job in second_page] == full_ids[start + 2 : start + 4]
            assert {job.id for job in first_page}.isdisjoint({job.id for job in second_page})

            our_ids_in_order = [
                job_id for job_id in full_ids[start : start + 4] if job_id in {j.id for j in jobs}
            ]
            assert our_ids_in_order == [jobs[0].id, jobs[1].id, jobs[2].id]
        finally:
            for job in jobs:
                _delete_job(db_session, job.id)

    def test_list_by_status_returns_empty_list_when_no_match(self, db_session: Session) -> None:
        repository = SQLAlchemyJobRepository(db_session)
        job = _make_job()

        try:
            repository.save(job)
            db_session.commit()

            assert repository.list_by_status(JobStatus.SENT) == []
        finally:
            _delete_job(db_session, job.id)
