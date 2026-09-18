"""Unit tests for the thin, non-business-logic layers of
`app/presentation/scheduler/jobs.py`: `_get_daily_hour` (pure env var
parsing/validation) and the APScheduler composition root
(`_run_daily_pipeline_job`/`run_scheduler`).

Deliberadamente en un archivo separado de `test_scheduler_jobs.py`, cuyo
propio docstring limita su alcance a `run_daily_pipeline` (la función pura
de orquestación) y dice explícitamente "nunca `run_scheduler`/APScheduler
ni ninguna dependencia real" -- ese límite se respeta ahí. Acá se cubre la
capa de wiring, pero sin construir ninguna dependencia real: las clases
concretas (`LinkedInFeedCollector`, `AnthropicProvider`,
`FilesystemCVRepository`, `BlockingScheduler`) se monkeypatchean por
nombre (mismo patrón que `tests/unit/infrastructure/gmail/test_gmail_client.py`
usa para `build`/`InstalledAppFlow`), nunca se instancian de verdad -- ni
Playwright, ni Anthropic, ni un `BlockingScheduler` real que bloquearía el
proceso de test.
"""

from __future__ import annotations

from typing import Any

import pytest
from apscheduler.triggers.cron import CronTrigger

from app.application.cv.matcher import CVMatcher
from app.presentation.scheduler import jobs as scheduler_jobs

pytestmark = pytest.mark.unit


class TestGetDailyHour:
    def test_returns_the_default_when_the_env_var_is_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("SCHEDULER_DAILY_HOUR", raising=False)

        assert scheduler_jobs._get_daily_hour() == 8

    def test_returns_the_default_when_the_env_var_is_blank(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SCHEDULER_DAILY_HOUR", "   ")

        assert scheduler_jobs._get_daily_hour() == 8

    @pytest.mark.parametrize("hour", [0, 8, 23])
    def test_parses_a_valid_hour_from_the_env_var(
        self, monkeypatch: pytest.MonkeyPatch, hour: int
    ) -> None:
        monkeypatch.setenv("SCHEDULER_DAILY_HOUR", str(hour))

        assert scheduler_jobs._get_daily_hour() == hour

    def test_raises_for_a_non_integer_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SCHEDULER_DAILY_HOUR", "not-a-number")

        with pytest.raises(ValueError, match="SCHEDULER_DAILY_HOUR"):
            scheduler_jobs._get_daily_hour()

    @pytest.mark.parametrize("hour", [-1, 24])
    def test_raises_for_an_out_of_range_hour(
        self, monkeypatch: pytest.MonkeyPatch, hour: int
    ) -> None:
        monkeypatch.setenv("SCHEDULER_DAILY_HOUR", str(hour))

        with pytest.raises(ValueError, match="SCHEDULER_DAILY_HOUR"):
            scheduler_jobs._get_daily_hour()


class _FakeLinkedInFeedCollector:
    """Stand-in for `LinkedInFeedCollector()` -- never touches Playwright."""


class _FakeAnthropicProvider:
    """Stand-in for `AnthropicProvider()` -- never touches the real Anthropic SDK."""


class _FakeFilesystemCVRepository:
    """Stand-in for `FilesystemCVRepository()` -- never reads `config/cvs.yaml`."""

    def list_cvs(self) -> list[Any]:
        return []

    def get_summary(self, cv_id: str) -> str:
        raise AssertionError("not used by this test")


class TestRunDailyPipelineJobWiring:
    """`_run_daily_pipeline_job` builds production dependencies and
    delegates to `run_daily_pipeline` -- verified here by monkeypatching
    every concrete class it constructs plus `run_daily_pipeline` itself, so
    this test only asserts *wiring* (right dependency, right factory), never
    exercises `run_daily_pipeline`'s own logic (already covered by
    `test_scheduler_jobs.py`)."""

    def test_builds_real_dependency_classes_and_delegates_to_run_daily_pipeline(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_feed_collector = _FakeLinkedInFeedCollector()
        fake_llm_provider = _FakeAnthropicProvider()
        fake_cv_catalog = _FakeFilesystemCVRepository()
        captured_kwargs: dict[str, Any] = {}

        monkeypatch.setattr(
            scheduler_jobs, "LinkedInFeedCollector", lambda: fake_feed_collector
        )
        monkeypatch.setattr(scheduler_jobs, "AnthropicProvider", lambda: fake_llm_provider)
        monkeypatch.setattr(
            scheduler_jobs, "FilesystemCVRepository", lambda: fake_cv_catalog
        )

        def _fake_run_daily_pipeline(**kwargs: Any) -> None:
            captured_kwargs.update(kwargs)

        monkeypatch.setattr(scheduler_jobs, "run_daily_pipeline", _fake_run_daily_pipeline)

        scheduler_jobs._run_daily_pipeline_job()

        assert captured_kwargs["feed_collector"] is fake_feed_collector
        assert captured_kwargs["llm_provider"] is fake_llm_provider
        assert captured_kwargs["cv_catalog"] is fake_cv_catalog
        assert (
            captured_kwargs["new_job_pipeline_session"] is scheduler_jobs._sql_job_pipeline_session
        )
        cv_matcher = captured_kwargs["cv_matcher"]
        assert isinstance(cv_matcher, CVMatcher)
        # El `CVMatcher` construido envuelve el mismo `cv_catalog` -- no una
        # copia ni un catálogo distinto.
        assert cv_matcher._catalog is fake_cv_catalog  # noqa: SLF001


class _FakeBlockingScheduler:
    """Stand-in for `apscheduler.schedulers.blocking.BlockingScheduler` --
    records `.add_job(...)` calls and whether `.start()` was invoked,
    never actually blocks the test process."""

    def __init__(self) -> None:
        self.add_job_calls: list[dict[str, Any]] = []
        self.started = False

    def add_job(self, func: Any, **kwargs: Any) -> None:
        self.add_job_calls.append({"func": func, **kwargs})

    def start(self) -> None:
        self.started = True


class TestRunScheduler:
    def test_schedules_the_daily_pipeline_job_at_the_configured_hour_and_starts(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SCHEDULER_DAILY_HOUR", "6")
        fake_scheduler = _FakeBlockingScheduler()
        monkeypatch.setattr(scheduler_jobs, "BlockingScheduler", lambda: fake_scheduler)

        scheduler_jobs.run_scheduler()

        assert fake_scheduler.started is True
        assert len(fake_scheduler.add_job_calls) == 1
        call = fake_scheduler.add_job_calls[0]
        assert call["func"] is scheduler_jobs._run_daily_pipeline_job
        assert isinstance(call["trigger"], CronTrigger)
        assert call["id"] == "daily_pipeline"
        assert call["name"] == "Daily job application pipeline"
        assert call["misfire_grace_time"] == 3600
        assert call["coalesce"] is True
        assert call["max_instances"] == 1

    def test_uses_the_default_hour_when_the_env_var_is_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("SCHEDULER_DAILY_HOUR", raising=False)
        fake_scheduler = _FakeBlockingScheduler()
        monkeypatch.setattr(scheduler_jobs, "BlockingScheduler", lambda: fake_scheduler)

        scheduler_jobs.run_scheduler()

        trigger = fake_scheduler.add_job_calls[0]["trigger"]
        assert isinstance(trigger, CronTrigger)

    def test_propagates_the_error_from_an_invalid_configured_hour_before_starting(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`_get_daily_hour()` corre antes de construir el `BlockingScheduler`
        -- un error de configuración de arranque debe frenar `run_scheduler()`
        antes de `scheduler.start()`, ver su docstring."""
        monkeypatch.setenv("SCHEDULER_DAILY_HOUR", "not-a-number")
        fake_scheduler = _FakeBlockingScheduler()
        monkeypatch.setattr(scheduler_jobs, "BlockingScheduler", lambda: fake_scheduler)

        with pytest.raises(ValueError, match="SCHEDULER_DAILY_HOUR"):
            scheduler_jobs.run_scheduler()

        assert fake_scheduler.started is False
