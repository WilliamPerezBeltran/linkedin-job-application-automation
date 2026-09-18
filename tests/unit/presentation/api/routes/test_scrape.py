"""Unit tests for `POST /scrape` (`app/presentation/api/routes/scrape.py`).

Sustituye `LinkedInFeedCollector`, `SQLAlchemyJobRepository` y
`session_scope` por dobles en memoria vía `monkeypatch`, aplicados sobre el
namespace del propio módulo `scrape` (composition root sin `Depends`,
construye sus dependencias directamente) — nunca Playwright ni PostgreSQL
real en unit tests.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app.application.dto.raw_feed_post import RawFeedPost
from app.domain.entities.job import Job
from app.domain.value_objects.job_id import JobId
from app.infrastructure.linkedin.exceptions import (
    LinkedInAuthenticationError,
    LinkedInBlockedError,
)
from app.main import app
from app.presentation.api.routes import scrape as scrape_module

pytestmark = pytest.mark.unit

client = TestClient(app)

_NOW = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)


class _FakeFeedCollector:
    def __init__(
        self, posts: list[RawFeedPost] | None = None, error: Exception | None = None
    ) -> None:
        self._posts = posts or []
        self._error = error

    def collect(self) -> list[RawFeedPost]:
        if self._error is not None:
            raise self._error
        return self._posts


class _InMemoryJobRepository:
    """Stands in for `SQLAlchemyJobRepository`; ignores the `session` arg."""

    def __init__(self, session: object) -> None:
        self._jobs_by_hash: dict[str, Job] = {}

    def save(self, job: Job) -> None:
        self._jobs_by_hash[job.content_hash] = job

    def get_by_id(self, job_id: JobId) -> Job | None:
        return None

    def get_by_content_hash(self, content_hash: str) -> Job | None:
        return self._jobs_by_hash.get(content_hash)


class _RaceConditionJobRepository(_InMemoryJobRepository):
    """Simulates two concurrent `/scrape` runs racing on the same `content_hash`.

    `get_by_content_hash` always misses (as it would for two requests that
    both ran their dedup check before either persisted anything), and
    `save()` raises `IntegrityError` — what `SQLAlchemyJobRepository.save()`
    would surface from its `flush()` if the unique constraint on
    `content_hash` were violated by the other concurrent request.
    """

    def save(self, job: Job) -> None:
        raise IntegrityError("INSERT INTO jobs ...", {}, Exception("duplicate key value"))


@contextmanager
def _fake_session_scope() -> Iterator[object]:
    yield object()


def _make_raw_post(**overrides: object) -> RawFeedPost:
    defaults: dict[str, object] = {
        "author": "Jane Recruiter",
        "content": "We are hiring a Senior Python Engineer...",
        "content_hash": "a" * 64,
        "url": "https://www.linkedin.com/feed/update/urn:li:activity:123/",
        "published_at": _NOW,
    }
    defaults.update(overrides)
    return RawFeedPost(**defaults)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _patch_persistence(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test in this module runs against in-memory persistence."""
    monkeypatch.setattr(scrape_module, "session_scope", _fake_session_scope)
    monkeypatch.setattr(scrape_module, "SQLAlchemyJobRepository", _InMemoryJobRepository)


class TestScrapeFeedHappyPath:
    def test_returns_200_with_the_use_case_counters(self, monkeypatch: pytest.MonkeyPatch) -> None:
        posts = [_make_raw_post()]
        monkeypatch.setattr(
            scrape_module, "LinkedInFeedCollector", lambda: _FakeFeedCollector(posts=posts)
        )

        response = client.post("/scrape")

        assert response.status_code == 200
        assert response.json() == {"saved": 1, "skipped_duplicates": 0, "skipped_invalid": 0}


class TestScrapeFeedErrorTranslation:
    def test_authentication_error_maps_to_401(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            scrape_module,
            "LinkedInFeedCollector",
            lambda: _FakeFeedCollector(error=LinkedInAuthenticationError("no session")),
        )

        response = client.post("/scrape")

        assert response.status_code == 401

    def test_other_linkedin_infrastructure_errors_map_to_502(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            scrape_module,
            "LinkedInFeedCollector",
            lambda: _FakeFeedCollector(error=LinkedInBlockedError("checkpoint detected")),
        )

        response = client.post("/scrape")

        assert response.status_code == 502

    def test_content_hash_race_condition_maps_to_409(self, monkeypatch: pytest.MonkeyPatch) -> None:
        posts = [_make_raw_post()]
        monkeypatch.setattr(
            scrape_module, "LinkedInFeedCollector", lambda: _FakeFeedCollector(posts=posts)
        )
        monkeypatch.setattr(scrape_module, "SQLAlchemyJobRepository", _RaceConditionJobRepository)

        response = client.post("/scrape")

        assert response.status_code == 409
