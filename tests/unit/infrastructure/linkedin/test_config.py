"""Unit tests for `app.infrastructure.linkedin.config.load_config_from_env`
and its two helpers (`_parse_bool`/`_parse_optional_int`).

Pura lectura/parseo de variables de entorno (`os.getenv` + `str`/`int`/
`float`/`bool`), sin filesystem ni red -- ningún test acá toca Playwright ni
`linkedin.com` real, mismo criterio que
`tests/unit/presentation/scheduler/test_scheduler_wiring.py::TestGetDailyHour`
para `_get_daily_hour()` (env var parsing análogo en otro módulo).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.infrastructure.linkedin.config import load_config_from_env

pytestmark = pytest.mark.unit

_ENV_VARS = (
    "LINKEDIN_STORAGE_STATE_PATH",
    "LINKEDIN_HEADLESS",
    "LINKEDIN_EMAIL",
    "LINKEDIN_PASSWORD",
    "LINKEDIN_LOGIN_TIMEOUT_SECONDS",
    "LINKEDIN_FEED_URL",
    "LINKEDIN_MAX_SCROLL_ITERATIONS",
    "LINKEDIN_SCROLL_DELAY_SECONDS",
    "LINKEDIN_MAX_POSTS",
)


@pytest.fixture(autouse=True)
def _clear_linkedin_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _ENV_VARS:
        monkeypatch.delenv(name, raising=False)


class TestLoadConfigFromEnvDefaults:
    def test_returns_hardcoded_defaults_when_no_env_vars_are_set(self) -> None:
        config = load_config_from_env()

        assert config.storage_state_path == Path("storage_state.json")
        assert config.headless is True
        assert config.login_email is None
        assert config.login_password is None
        assert config.login_timeout_seconds == 300
        assert config.feed_url == "https://www.linkedin.com/feed/"
        assert config.max_scroll_iterations == 5
        assert config.scroll_delay_seconds == pytest.approx(2.0)
        assert config.max_posts == 20


class TestLoadConfigFromEnvOverrides:
    def test_every_env_var_overrides_its_matching_field(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LINKEDIN_STORAGE_STATE_PATH", "custom_storage_state.json")
        monkeypatch.setenv("LINKEDIN_HEADLESS", "false")
        monkeypatch.setenv("LINKEDIN_EMAIL", "me@example.com")
        monkeypatch.setenv("LINKEDIN_PASSWORD", "hunter2")
        monkeypatch.setenv("LINKEDIN_LOGIN_TIMEOUT_SECONDS", "120")
        monkeypatch.setenv("LINKEDIN_FEED_URL", "https://www.linkedin.com/feed/custom/")
        monkeypatch.setenv("LINKEDIN_MAX_SCROLL_ITERATIONS", "9")
        monkeypatch.setenv("LINKEDIN_SCROLL_DELAY_SECONDS", "1.5")
        monkeypatch.setenv("LINKEDIN_MAX_POSTS", "42")

        config = load_config_from_env()

        assert config.storage_state_path == Path("custom_storage_state.json")
        assert config.headless is False
        assert config.login_email == "me@example.com"
        assert config.login_password == "hunter2"
        assert config.login_timeout_seconds == 120
        assert config.feed_url == "https://www.linkedin.com/feed/custom/"
        assert config.max_scroll_iterations == 9
        assert config.scroll_delay_seconds == pytest.approx(1.5)
        assert config.max_posts == 42

    def test_blank_email_and_password_env_vars_become_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LINKEDIN_EMAIL", "")
        monkeypatch.setenv("LINKEDIN_PASSWORD", "")

        config = load_config_from_env()

        assert config.login_email is None
        assert config.login_password is None

    def test_blank_max_posts_env_var_means_unbounded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LINKEDIN_MAX_POSTS", "   ")

        config = load_config_from_env()

        assert config.max_posts is None


class TestLoadConfigFromEnvHeadlessParsing:
    """`_parse_bool` -- ejercitada indirectamente vía `LINKEDIN_HEADLESS`,
    ver su docstring: `None`/blank cae al default, y solo
    `{"0", "false", "no"}` (case-insensitive) se interpreta como falso;
    cualquier otro valor no vacío es verdadero."""

    @pytest.mark.parametrize("raw_value", ["0", "false", "False", "FALSE", "no", "No"])
    def test_falsy_values_disable_headless(
        self, monkeypatch: pytest.MonkeyPatch, raw_value: str
    ) -> None:
        monkeypatch.setenv("LINKEDIN_HEADLESS", raw_value)

        assert load_config_from_env().headless is False

    @pytest.mark.parametrize("raw_value", ["1", "true", "True", "yes", "anything-else"])
    def test_other_non_blank_values_enable_headless(
        self, monkeypatch: pytest.MonkeyPatch, raw_value: str
    ) -> None:
        monkeypatch.setenv("LINKEDIN_HEADLESS", raw_value)

        assert load_config_from_env().headless is True

    def test_blank_value_falls_back_to_the_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LINKEDIN_HEADLESS", "   ")

        assert load_config_from_env().headless is True
