"""Unit tests for `CollectFeedPosts`.

Usa un `FakeFeedCollector` y un `InMemoryJobRepository`, ambos en memoria,
implementando `FeedCollector`/`JobRepository` por structural typing — nunca
Playwright ni PostgreSQL real en unit tests (ver `docs/agents/AGENTS.md`
sección 8).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import pytest

from app.application.dto.raw_feed_post import RawFeedPost
from app.application.use_cases.collect_feed_posts import CollectFeedPosts
from app.domain.entities.job import Job
from app.domain.value_objects.job_id import JobId
from app.domain.value_objects.job_status import JobStatus

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)


class FakeFeedCollector:
    """In-memory `FeedCollector`: returns whatever posts it was given."""

    def __init__(self, posts: list[RawFeedPost]) -> None:
        self._posts = posts

    def collect(self) -> list[RawFeedPost]:
        return list(self._posts)


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
        """Added for `JobRepository` Protocol conformance (ADR-004 §1).

        No la ejercita ningún test de este módulo todavía (`CollectFeedPosts`
        no la usa) — solo existe para que este fake siga satisfaciendo el
        Protocol completo bajo `mypy --strict`, igual que
        `SQLAlchemyJobRepository` ya la implementa en infraestructura.
        """
        matching = sorted(
            (job for job in self._jobs_by_id.values() if job.status == status),
            key=lambda job: job.created_at,
        )
        return matching[offset : offset + limit]

    def count(self) -> int:
        return len(self._jobs_by_id)


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


class TestCollectFeedPostsSavesNewPosts:
    def test_saves_each_new_post_as_a_scraped_job(self) -> None:
        posts = [
            _make_raw_post(content_hash="a" * 64, url="https://www.linkedin.com/feed/update/1/"),
            _make_raw_post(content_hash="b" * 64, url="https://www.linkedin.com/feed/update/2/"),
        ]
        repository = InMemoryJobRepository()
        use_case = CollectFeedPosts(
            feed_collector=FakeFeedCollector(posts), job_repository=repository
        )

        result = use_case.execute()

        assert result.saved == 2
        assert result.skipped_duplicates == 0
        assert result.skipped_invalid == 0
        assert repository.get_by_content_hash("a" * 64) is not None
        assert repository.get_by_content_hash("b" * 64) is not None


class TestCollectFeedPostsDeduplicatesByContentHash:
    def test_running_twice_with_the_same_posts_does_not_duplicate(self) -> None:
        posts = [_make_raw_post(content_hash="a" * 64)]
        repository = InMemoryJobRepository()
        collector = FakeFeedCollector(posts)

        first_run = CollectFeedPosts(feed_collector=collector, job_repository=repository)
        first_result = first_run.execute()

        second_run = CollectFeedPosts(feed_collector=collector, job_repository=repository)
        second_result = second_run.execute()

        assert first_result.saved == 1
        assert second_result.saved == 0
        assert second_result.skipped_duplicates == 1
        assert repository.count() == 1

    def test_never_reinserts_or_updates_the_existing_job(self) -> None:
        posts = [_make_raw_post(content_hash="a" * 64, author="Original Author")]
        repository = InMemoryJobRepository()
        collector = FakeFeedCollector(posts)
        CollectFeedPosts(feed_collector=collector, job_repository=repository).execute()
        existing_job = repository.get_by_content_hash("a" * 64)
        assert existing_job is not None
        original_id = existing_job.id

        duplicate_posts = [_make_raw_post(content_hash="a" * 64, author="Different Author")]
        CollectFeedPosts(
            feed_collector=FakeFeedCollector(duplicate_posts), job_repository=repository
        ).execute()

        unchanged_job = repository.get_by_content_hash("a" * 64)
        assert unchanged_job is not None
        assert unchanged_job.id == original_id
        assert unchanged_job.author == "Original Author"


class TestCollectFeedPostsSkipsInvalidPostsWithoutAbortingTheBatch:
    def test_post_with_empty_url_is_skipped_and_others_are_still_saved(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        posts = [
            _make_raw_post(content_hash="a" * 64, url=""),
            _make_raw_post(content_hash="b" * 64, url="https://www.linkedin.com/feed/update/2/"),
            _make_raw_post(content_hash="c" * 64, url="https://www.linkedin.com/feed/update/3/"),
        ]
        repository = InMemoryJobRepository()
        use_case = CollectFeedPosts(
            feed_collector=FakeFeedCollector(posts), job_repository=repository
        )

        with caplog.at_level(logging.WARNING):
            result = use_case.execute()

        assert result.saved == 2
        assert result.skipped_invalid == 1
        assert result.skipped_duplicates == 0
        assert repository.get_by_content_hash("a" * 64) is None
        assert repository.get_by_content_hash("b" * 64) is not None
        assert repository.get_by_content_hash("c" * 64) is not None
        assert any(
            record.levelno == logging.WARNING and "a" * 64 in record.getMessage()
            for record in caplog.records
        )
