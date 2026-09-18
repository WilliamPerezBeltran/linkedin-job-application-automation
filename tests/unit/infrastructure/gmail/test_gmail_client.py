"""Unit tests for `app.infrastructure.gmail.gmail_client`.

Ninguno de estos tests golpea la red real de Google ni abre un navegador:
`Credentials.from_authorized_user_file`, `Credentials.refresh`,
`InstalledAppFlow.from_client_secrets_file` y
`googleapiclient.discovery.build` se monkeypatchean siempre (mismo
criterio que `docs/agents/AGENTS.md` sección 16 exige para cualquier unit
test de este proyecto: nunca depende de un servicio externo real).
"""

from __future__ import annotations

import stat
from pathlib import Path
from typing import Any

import pytest
from google.auth.exceptions import RefreshError

from app.infrastructure.gmail import gmail_client
from app.infrastructure.gmail.exceptions import (
    GmailAuthenticationError,
    GmailCredentialsNotFoundError,
)

pytestmark = pytest.mark.unit


class _FakeCredentials:
    """Stand-in for `google.oauth2.credentials.Credentials`."""

    def __init__(
        self,
        *,
        valid: bool,
        expired: bool = False,
        refresh_token: str | None = None,
        json_content: str = '{"fake": "token"}',
        refresh_error: Exception | None = None,
    ) -> None:
        self.valid = valid
        self.expired = expired
        self.refresh_token = refresh_token
        self._json_content = json_content
        self._refresh_error = refresh_error
        self.refreshed = False

    def refresh(self, request: Any) -> None:
        self.refreshed = True
        if self._refresh_error is not None:
            raise self._refresh_error
        self.valid = True
        self.expired = False

    def to_json(self) -> str:
        return self._json_content


class _FakeFlow:
    def __init__(self, credentials: _FakeCredentials, *, error: Exception | None = None) -> None:
        self._credentials = credentials
        self._error = error
        self.run_local_server_called = False

    def run_local_server(self, *, port: int) -> _FakeCredentials:
        self.run_local_server_called = True
        if self._error is not None:
            raise self._error
        return self._credentials


def _assert_owner_only_permissions(path: Path) -> None:
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600


class TestResolveCredentialsAndTokenPathDefaults:
    """`resolve_credentials_path`/`resolve_token_path` (called by
    `build_gmail_service` when the caller doesn't override the path
    explicitly, e.g. `GmailDraftRepository()` with no arguments in
    production) fall back to `GMAIL_CREDENTIALS_PATH`/`GMAIL_TOKEN_PATH`,
    then to the hardcoded default -- pure `os.getenv` logic, no filesystem
    or network I/O, so no need to touch `build_gmail_service` at all."""

    def test_resolve_credentials_path_uses_explicit_argument_over_env_and_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GMAIL_CREDENTIALS_PATH", "from-env.json")

        assert gmail_client.resolve_credentials_path("explicit.json") == Path("explicit.json")

    def test_resolve_credentials_path_falls_back_to_env_var(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GMAIL_CREDENTIALS_PATH", "from-env.json")

        assert gmail_client.resolve_credentials_path(None) == Path("from-env.json")

    def test_resolve_credentials_path_falls_back_to_hardcoded_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("GMAIL_CREDENTIALS_PATH", raising=False)

        assert gmail_client.resolve_credentials_path(None) == Path("credentials.json")

    def test_resolve_token_path_uses_explicit_argument_over_env_and_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GMAIL_TOKEN_PATH", "from-env-token.json")

        assert gmail_client.resolve_token_path("explicit-token.json") == Path(
            "explicit-token.json"
        )

    def test_resolve_token_path_falls_back_to_env_var(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GMAIL_TOKEN_PATH", "from-env-token.json")

        assert gmail_client.resolve_token_path(None) == Path("from-env-token.json")

    def test_resolve_token_path_falls_back_to_hardcoded_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("GMAIL_TOKEN_PATH", raising=False)

        assert gmail_client.resolve_token_path(None) == Path("token.json")


class TestCredentialsNotFound:
    def test_missing_credentials_file_and_no_cached_token_raises(self, tmp_path: Path) -> None:
        with pytest.raises(GmailCredentialsNotFoundError):
            gmail_client.build_gmail_service(
                credentials_path=tmp_path / "credentials.json",
                token_path=tmp_path / "token.json",
            )

    def test_error_message_never_leaks_the_path_contents(self, tmp_path: Path) -> None:
        credentials_path = tmp_path / "credentials.json"
        with pytest.raises(GmailCredentialsNotFoundError) as exc_info:
            gmail_client.build_gmail_service(
                credentials_path=credentials_path, token_path=tmp_path / "token.json"
            )
        # El mensaje puede (y debe) mencionar la ruta configurada -- eso no
        # es un secreto -- pero nunca contenido de credenciales, porque el
        # archivo directamente no existe.
        assert str(credentials_path) in str(exc_info.value)


class TestCachedTokenReuse:
    def test_valid_cached_token_is_reused_without_refresh_or_interactive_flow(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        token_path = tmp_path / "token.json"
        token_path.write_text('{"fake": "cached-token"}', encoding="utf-8")
        fake_credentials = _FakeCredentials(valid=True)

        monkeypatch.setattr(
            gmail_client.Credentials,
            "from_authorized_user_file",
            classmethod(lambda cls, path, scopes: fake_credentials),
        )
        built_with: dict[str, Any] = {}

        def _fake_build(service: str, version: str, credentials: Any) -> str:
            built_with["credentials"] = credentials
            return "fake-service"

        monkeypatch.setattr(gmail_client, "build", _fake_build)

        def _fail_if_called(*args: Any, **kwargs: Any) -> None:
            raise AssertionError("InstalledAppFlow should never be invoked for a valid token")

        monkeypatch.setattr(
            gmail_client.InstalledAppFlow, "from_client_secrets_file", _fail_if_called
        )

        service = gmail_client.build_gmail_service(
            credentials_path=tmp_path / "credentials.json", token_path=token_path
        )

        assert service == "fake-service"
        assert built_with["credentials"] is fake_credentials
        assert fake_credentials.refreshed is False


class TestCorruptedCachedToken:
    """`Credentials.from_authorized_user_file` (el SDK real, sin
    monkeypatchear -- a propósito, para probar el caso real de un archivo
    con contenido inválido) lanza `ValueError`/`json.JSONDecodeError` si
    `token.json` no tiene el formato esperado -- p. ej. un crash a mitad de
    `_persist_token`, o una edición manual del usuario. Este escenario
    debía tratarse como "no hay token cacheado utilizable" (cae al flujo
    normal de reautorización), nunca dejar propagar el `ValueError` crudo
    del SDK (hallazgo de `code-reviewer`, revisión de Fase 7)."""

    def test_malformed_cached_token_falls_back_to_interactive_authorization(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        credentials_path = tmp_path / "credentials.json"
        credentials_path.write_text("{}", encoding="utf-8")
        token_path = tmp_path / "token.json"
        token_path.write_text("this is not valid JSON at all", encoding="utf-8")

        new_credentials = _FakeCredentials(valid=True, json_content='{"fake": "recovered"}')
        fake_flow = _FakeFlow(new_credentials)

        monkeypatch.setattr(
            gmail_client.InstalledAppFlow,
            "from_client_secrets_file",
            classmethod(lambda cls, path, scopes: fake_flow),
        )
        monkeypatch.setattr(
            gmail_client, "build", lambda service, version, credentials: "fake-service"
        )

        service = gmail_client.build_gmail_service(
            credentials_path=credentials_path, token_path=token_path
        )

        assert service == "fake-service"
        assert fake_flow.run_local_server_called is True
        assert token_path.read_text(encoding="utf-8") == '{"fake": "recovered"}'
        _assert_owner_only_permissions(token_path)

    def test_malformed_cached_token_without_credentials_file_raises_credentials_not_found(
        self, tmp_path: Path
    ) -> None:
        token_path = tmp_path / "token.json"
        token_path.write_text("this is not valid JSON at all", encoding="utf-8")

        with pytest.raises(GmailCredentialsNotFoundError):
            gmail_client.build_gmail_service(
                credentials_path=tmp_path / "credentials.json", token_path=token_path
            )

    def test_valid_json_but_non_dict_cached_token_falls_back_to_interactive_authorization(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`token.json` con JSON válido pero no un objeto (`[]`, `null`,
        `42`, `"texto"`) -- el SDK real lanza `AttributeError` (no
        `ValueError`) al intentar `.keys()` sobre ese valor. Ver hallazgo
        adicional de `code-reviewer`, segunda ronda de revisión de Fase 7.
        """
        credentials_path = tmp_path / "credentials.json"
        credentials_path.write_text("{}", encoding="utf-8")
        token_path = tmp_path / "token.json"
        token_path.write_text("[]", encoding="utf-8")

        new_credentials = _FakeCredentials(valid=True, json_content='{"fake": "recovered"}')
        fake_flow = _FakeFlow(new_credentials)

        monkeypatch.setattr(
            gmail_client.InstalledAppFlow,
            "from_client_secrets_file",
            classmethod(lambda cls, path, scopes: fake_flow),
        )
        monkeypatch.setattr(
            gmail_client, "build", lambda service, version, credentials: "fake-service"
        )

        service = gmail_client.build_gmail_service(
            credentials_path=credentials_path, token_path=token_path
        )

        assert service == "fake-service"
        assert fake_flow.run_local_server_called is True
        assert token_path.read_text(encoding="utf-8") == '{"fake": "recovered"}'


class TestTokenRefresh:
    def test_expired_token_is_refreshed_and_persisted_with_owner_only_permissions(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        token_path = tmp_path / "token.json"
        token_path.write_text('{"fake": "expired-token"}', encoding="utf-8")
        fake_credentials = _FakeCredentials(
            valid=False, expired=True, refresh_token="rt", json_content='{"fake": "refreshed"}'
        )

        monkeypatch.setattr(
            gmail_client.Credentials,
            "from_authorized_user_file",
            classmethod(lambda cls, path, scopes: fake_credentials),
        )
        monkeypatch.setattr(
            gmail_client, "build", lambda service, version, credentials: "fake-service"
        )

        service = gmail_client.build_gmail_service(
            credentials_path=tmp_path / "credentials.json", token_path=token_path
        )

        assert service == "fake-service"
        assert fake_credentials.refreshed is True
        assert token_path.read_text(encoding="utf-8") == '{"fake": "refreshed"}'
        _assert_owner_only_permissions(token_path)

    def test_refresh_failure_falls_back_to_interactive_authorization(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        credentials_path = tmp_path / "credentials.json"
        credentials_path.write_text("{}", encoding="utf-8")
        token_path = tmp_path / "token.json"
        token_path.write_text('{"fake": "stale-token"}', encoding="utf-8")

        stale_credentials = _FakeCredentials(
            valid=False, expired=True, refresh_token="rt", refresh_error=RefreshError("revoked")
        )
        new_credentials = _FakeCredentials(valid=True, json_content='{"fake": "brand-new"}')
        fake_flow = _FakeFlow(new_credentials)

        monkeypatch.setattr(
            gmail_client.Credentials,
            "from_authorized_user_file",
            classmethod(lambda cls, path, scopes: stale_credentials),
        )
        monkeypatch.setattr(
            gmail_client.InstalledAppFlow,
            "from_client_secrets_file",
            classmethod(lambda cls, path, scopes: fake_flow),
        )
        monkeypatch.setattr(
            gmail_client, "build", lambda service, version, credentials: "fake-service"
        )

        service = gmail_client.build_gmail_service(
            credentials_path=credentials_path, token_path=token_path
        )

        assert service == "fake-service"
        assert stale_credentials.refreshed is True
        assert fake_flow.run_local_server_called is True
        assert token_path.read_text(encoding="utf-8") == '{"fake": "brand-new"}'
        _assert_owner_only_permissions(token_path)


class TestFirstTimeAuthorization:
    def test_no_cached_token_runs_interactive_flow_and_persists_token(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        credentials_path = tmp_path / "credentials.json"
        credentials_path.write_text("{}", encoding="utf-8")
        token_path = tmp_path / "token.json"
        new_credentials = _FakeCredentials(valid=True, json_content='{"fake": "first-token"}')
        fake_flow = _FakeFlow(new_credentials)

        monkeypatch.setattr(
            gmail_client.InstalledAppFlow,
            "from_client_secrets_file",
            classmethod(lambda cls, path, scopes: fake_flow),
        )
        monkeypatch.setattr(
            gmail_client, "build", lambda service, version, credentials: "fake-service"
        )

        service = gmail_client.build_gmail_service(
            credentials_path=credentials_path, token_path=token_path
        )

        assert service == "fake-service"
        assert fake_flow.run_local_server_called is True
        assert token_path.read_text(encoding="utf-8") == '{"fake": "first-token"}'
        _assert_owner_only_permissions(token_path)

    def test_interactive_flow_failure_is_wrapped_without_leaking_original_details(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        credentials_path = tmp_path / "credentials.json"
        credentials_path.write_text("{}", encoding="utf-8")
        token_path = tmp_path / "token.json"

        sensitive_original_error = RuntimeError("client_secret=super-secret-value-should-not-leak")
        fake_flow = _FakeFlow(_FakeCredentials(valid=True), error=sensitive_original_error)

        monkeypatch.setattr(
            gmail_client.InstalledAppFlow,
            "from_client_secrets_file",
            classmethod(lambda cls, path, scopes: fake_flow),
        )

        with pytest.raises(GmailAuthenticationError) as exc_info:
            gmail_client.build_gmail_service(
                credentials_path=credentials_path, token_path=token_path
            )

        assert "super-secret-value-should-not-leak" not in str(exc_info.value)
        assert isinstance(exc_info.value.__cause__, RuntimeError)
        assert not token_path.exists()
