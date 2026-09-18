"""Unit tests for `app.infrastructure.gmail.gmail_draft_repository.GmailDraftRepository`.

Ninguno de estos tests construye un `googleapiclient.discovery.Resource`
real ni golpea la red/OAuth: todos inyectan un `drafts_client` de prueba
(`FakeDraftsClient`, satisface `_DraftsClient` por forma) en el
constructor, mismo patrón que
`tests/unit/infrastructure/llm/test_anthropic_provider.py` usa para
`_CompletionClient` (`docs/agents/AGENTS.md` sección 16: unit tests nunca
llaman a Gmail real).
"""

from __future__ import annotations

import base64
import email
import os
import stat
from email.header import decode_header, make_header
from email.message import Message
from pathlib import Path
from typing import Any

import httplib2
import pytest
from googleapiclient.errors import HttpError

from app.domain.value_objects.email_address import EmailAddress
from app.infrastructure.gmail import gmail_draft_repository as gmail_draft_repository_module
from app.infrastructure.gmail.exceptions import (
    GmailAttachmentNotFoundError,
    GmailAttachmentPathTraversalError,
    GmailDraftCreationError,
    GmailInvalidMessageContentError,
)
from app.infrastructure.gmail.gmail_draft_repository import (
    GmailDraftRepository,
    _GoogleAPIDraftsClient,
)

pytestmark = pytest.mark.unit

_FAKE_PDF_BYTES = b"%PDF-1.4 fake cv content for tests\n"


class FakeDraftsClient:
    """Test double for `_DraftsClient`: records every call and returns a
    fixed response, or raises a pre-configured exception."""

    def __init__(self, draft_id: str = "draft-123", error: Exception | None = None) -> None:
        self._draft_id = draft_id
        self._error = error
        self.calls: list[str] = []

    def create_draft(self, *, raw_message: str) -> dict[str, Any]:
        self.calls.append(raw_message)
        if self._error is not None:
            raise self._error
        return {"id": self._draft_id}


def _write_fake_cv(base_dir: Path, relative_path: str = "cvs/java/cv.pdf") -> Path:
    cv_path = base_dir / relative_path
    cv_path.parent.mkdir(parents=True, exist_ok=True)
    cv_path.write_bytes(_FAKE_PDF_BYTES)
    return cv_path


def _decode_raw_message(raw_message: str) -> Message:
    decoded_bytes = base64.urlsafe_b64decode(raw_message.encode("ascii"))
    return email.message_from_bytes(decoded_bytes)


def _http_error(status: str = "400", reason: str = "Bad Request") -> HttpError:
    resp = httplib2.Response({"status": status})
    resp.reason = reason
    return HttpError(resp, b'{"error": "simulated Gmail API failure"}')


class TestCreateDraftHappyPath:
    def test_builds_mime_message_with_attachment_and_returns_draft_id(self, tmp_path: Path) -> None:
        _write_fake_cv(tmp_path)
        drafts_client = FakeDraftsClient(draft_id="draft-abc")
        repository = GmailDraftRepository(drafts_client=drafts_client, base_dir=tmp_path)

        draft_id = repository.create_draft(
            to=EmailAddress("jobs@example.com"),
            subject="Postulación - Backend Developer",
            body="Estimados,\n\nMe postulo a la posición.\n\nSaludos.",
            cv_path="cvs/java/cv.pdf",
        )

        assert draft_id == "draft-abc"
        assert len(drafts_client.calls) == 1

    def test_message_headers_body_and_attachment_are_correct(self, tmp_path: Path) -> None:
        _write_fake_cv(tmp_path)
        drafts_client = FakeDraftsClient()
        repository = GmailDraftRepository(drafts_client=drafts_client, base_dir=tmp_path)

        repository.create_draft(
            to=EmailAddress("jobs@example.com"),
            subject="Postulación - Backend Developer",
            body="Estimados,\n\nMe postulo a la posición.\n\nSaludos.",
            cv_path="cvs/java/cv.pdf",
        )

        message = _decode_raw_message(drafts_client.calls[0])
        assert message["to"] == "jobs@example.com"
        # El subject se codifica RFC 2047 (`=?utf-8?q?...?=`) por tener
        # tildes -- se decodifica antes de comparar el texto real.
        decoded_subject = str(make_header(decode_header(message["subject"])))
        assert decoded_subject == "Postulación - Backend Developer"
        assert message.is_multipart()

        parts = message.get_payload()
        assert isinstance(parts, list)

        text_parts = [p for p in parts if p.get_content_type() == "text/plain"]
        assert len(text_parts) == 1
        assert (
            text_parts[0].get_payload(decode=True)
            == ("Estimados,\n\nMe postulo a la posición.\n\nSaludos.").encode()
        )

        attachment_parts = [p for p in parts if p.get_filename() == "cv.pdf"]
        assert len(attachment_parts) == 1
        assert attachment_parts[0].get_content_type() == "application/pdf"
        assert attachment_parts[0].get_payload(decode=True) == _FAKE_PDF_BYTES
        assert "attachment" in attachment_parts[0].get("Content-Disposition", "")

    def test_calls_only_drafts_create_never_a_send_endpoint(self, tmp_path: Path) -> None:
        """Restricción no negociable (`ROADMAP.md` Fase 7 / AGENTS.md
        sección 12): esta clase no expone ningún método de envío, ni
        siquiera indirectamente."""
        _write_fake_cv(tmp_path)
        repository = GmailDraftRepository(drafts_client=FakeDraftsClient(), base_dir=tmp_path)

        assert not hasattr(repository, "send")
        assert not hasattr(repository, "send_draft")
        assert not hasattr(repository, "send_message")


class TestCreateDraftErrorHandling:
    def test_gmail_api_http_error_is_wrapped_and_never_swallowed(self, tmp_path: Path) -> None:
        _write_fake_cv(tmp_path)
        drafts_client = FakeDraftsClient(error=_http_error())
        repository = GmailDraftRepository(drafts_client=drafts_client, base_dir=tmp_path)

        with pytest.raises(GmailDraftCreationError):
            repository.create_draft(
                to=EmailAddress("jobs@example.com"),
                subject="Subject",
                body="Body",
                cv_path="cvs/java/cv.pdf",
            )

        # La excepción original del SDK nunca se propaga como tipo público.
        assert len(drafts_client.calls) == 1

    def test_response_without_a_usable_id_raises_draft_creation_error(self, tmp_path: Path) -> None:
        _write_fake_cv(tmp_path)

        class _MissingIdDraftsClient:
            def create_draft(self, *, raw_message: str) -> dict[str, Any]:
                return {"message": {"id": "irrelevant"}}

        repository = GmailDraftRepository(drafts_client=_MissingIdDraftsClient(), base_dir=tmp_path)

        with pytest.raises(GmailDraftCreationError):
            repository.create_draft(
                to=EmailAddress("jobs@example.com"),
                subject="Subject",
                body="Body",
                cv_path="cvs/java/cv.pdf",
            )

    @pytest.mark.parametrize(
        "control_character",
        [
            pytest.param("\r\n", id="crlf"),
            pytest.param("\n", id="lf-only"),
            pytest.param("\x0b", id="vertical-tab"),
            pytest.param("\x0c", id="form-feed"),
            pytest.param("\x1c", id="information-separator-four"),
        ],
    )
    def test_subject_with_embedded_control_character_is_rejected_before_calling_gmail_api(
        self, tmp_path: Path, control_character: str
    ) -> None:
        """Hallazgo de `security-agent` (revisión de seguridad de Fase 7),
        ampliado por `code-reviewer` en la misma ronda: `subject` lo produce
        el LLM, que solo valida "no vacío tras `strip()`" -- un carácter de
        control Unicode (categoría `Cc`) embebido en medio del texto
        sobrevive esa validación, no solo `\\r`/`\\n`: `\\x0b`/`\\x0c`/
        `\\x1c`-`\\x1e` disparan el mismo problema en el stdlib `email`
        (`HeaderParseError`/`HeaderWriteError`, no envuelto por
        `GmailInfrastructureError`, con el subject crudo en su mensaje) --
        ver `_reject_embedded_control_characters` en
        `gmail_draft_repository.py`, que por eso chequea la categoría
        Unicode completa en vez de enumerar caracteres a mano."""
        _write_fake_cv(tmp_path)
        drafts_client = FakeDraftsClient()
        repository = GmailDraftRepository(drafts_client=drafts_client, base_dir=tmp_path)

        malicious_subject = f"Postulación{control_character}Bcc: attacker@evil.com"
        with pytest.raises(GmailInvalidMessageContentError) as exc_info:
            repository.create_draft(
                to=EmailAddress("jobs@example.com"),
                subject=malicious_subject,
                body="Body",
                cv_path="cvs/java/cv.pdf",
            )

        # Nunca se llega a invocar la API de Gmail con un mensaje corrupto.
        assert drafts_client.calls == []
        # El mensaje de la excepción nunca repite el contenido rechazado.
        assert "attacker@evil.com" not in str(exc_info.value)
        assert malicious_subject not in str(exc_info.value)

    def test_missing_cv_file_raises_before_calling_gmail_api(self, tmp_path: Path) -> None:
        drafts_client = FakeDraftsClient()
        repository = GmailDraftRepository(drafts_client=drafts_client, base_dir=tmp_path)

        with pytest.raises(GmailAttachmentNotFoundError):
            repository.create_draft(
                to=EmailAddress("jobs@example.com"),
                subject="Subject",
                body="Body",
                cv_path="cvs/java/does-not-exist.pdf",
            )

        assert drafts_client.calls == []

    @pytest.mark.skipif(
        hasattr(os, "geteuid") and os.geteuid() == 0,
        reason="root bypasses file permission checks, making this test meaningless",
    )
    def test_unreadable_cv_file_raises_before_calling_gmail_api(self, tmp_path: Path) -> None:
        cv_path = _write_fake_cv(tmp_path)
        cv_path.chmod(0o000)
        try:
            drafts_client = FakeDraftsClient()
            repository = GmailDraftRepository(drafts_client=drafts_client, base_dir=tmp_path)

            with pytest.raises(GmailAttachmentNotFoundError):
                repository.create_draft(
                    to=EmailAddress("jobs@example.com"),
                    subject="Subject",
                    body="Body",
                    cv_path="cvs/java/cv.pdf",
                )
        finally:
            # Restaurar permisos para que pytest pueda limpiar `tmp_path`.
            cv_path.chmod(stat.S_IRUSR | stat.S_IWUSR)

        assert drafts_client.calls == []

    def test_path_traversal_outside_base_dir_is_rejected_before_reading_the_file(
        self, tmp_path: Path
    ) -> None:
        # Archivo real fuera de `base_dir`, para demostrar que existiría si
        # no fuera por la defensa de path traversal.
        outside_dir = tmp_path.parent / "outside-cv-dir"
        outside_dir.mkdir(exist_ok=True)
        (outside_dir / "secret.pdf").write_bytes(_FAKE_PDF_BYTES)

        base_dir = tmp_path / "repo-root"
        base_dir.mkdir()

        drafts_client = FakeDraftsClient()
        repository = GmailDraftRepository(drafts_client=drafts_client, base_dir=base_dir)

        with pytest.raises(GmailAttachmentPathTraversalError):
            repository.create_draft(
                to=EmailAddress("jobs@example.com"),
                subject="Subject",
                body="Body",
                cv_path="../outside-cv-dir/secret.pdf",
            )

        assert drafts_client.calls == []


class TestBaseDirDefault:
    def test_defaults_to_current_working_directory(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.chdir(tmp_path)
        _write_fake_cv(tmp_path)
        drafts_client = FakeDraftsClient(draft_id="draft-cwd")

        repository = GmailDraftRepository(drafts_client=drafts_client)
        draft_id = repository.create_draft(
            to=EmailAddress("jobs@example.com"),
            subject="Subject",
            body="Body",
            cv_path="cvs/java/cv.pdf",
        )

        assert draft_id == "draft-cwd"


class _FakeDraftsResource:
    """Stand-in for the `service.users().drafts()` chain of a real
    `googleapiclient.discovery.Resource` -- records the exact `userId`/
    `body` passed to `.create(...)` and returns a fake request object whose
    `.execute()` yields a fixed response, without ever building a real
    Gmail API `Resource` (no network, no OAuth)."""

    def __init__(self, response: dict[str, Any]) -> None:
        self._response = response
        self.create_calls: list[dict[str, Any]] = []

    def create(self, *, userId: str, body: dict[str, Any]) -> _FakeExecutable:
        self.create_calls.append({"userId": userId, "body": body})
        return _FakeExecutable(self._response)


class _FakeExecutable:
    def __init__(self, response: dict[str, Any]) -> None:
        self._response = response

    def execute(self) -> dict[str, Any]:
        return self._response


class _FakeUsersResource:
    def __init__(self, drafts_resource: _FakeDraftsResource) -> None:
        self._drafts_resource = drafts_resource

    def drafts(self) -> _FakeDraftsResource:
        return self._drafts_resource


class _FakeGmailService:
    """Stand-in for the `Resource` returned by
    `gmail_client.build_gmail_service` -- exposes only `.users()`, the
    single method `_GoogleAPIDraftsClient` calls on it."""

    def __init__(self, response: dict[str, Any]) -> None:
        self.drafts_resource = _FakeDraftsResource(response)

    def users(self) -> _FakeUsersResource:
        return _FakeUsersResource(self.drafts_resource)


class TestGoogleAPIDraftsClient:
    """`_GoogleAPIDraftsClient` adapts a real, already-authorized `Resource`
    to `_DraftsClient` -- exercised here against a fake `Resource` double
    (never a real `googleapiclient.discovery.Resource`, which would need
    network/OAuth) to cover the actual `users().drafts().create(...).execute()`
    call chain that production code performs."""

    def test_create_draft_calls_users_drafts_create_and_returns_the_raw_response(self) -> None:
        service = _FakeGmailService({"id": "draft-from-real-adapter"})
        client = _GoogleAPIDraftsClient(service)

        response = client.create_draft(raw_message="ZmFrZS1yYXctbWVzc2FnZQ==")

        assert response == {"id": "draft-from-real-adapter"}
        assert service.drafts_resource.create_calls == [
            {
                "userId": "me",
                "body": {"message": {"raw": "ZmFrZS1yYXctbWVzc2FnZQ=="}},
            }
        ]


class TestGmailDraftRepositoryBuildsRealServiceWhenNoDraftsClientInjected:
    """`GmailDraftRepository()` without `drafts_client=` builds a real
    service via `gmail_client.build_gmail_service` and wraps it in
    `_GoogleAPIDraftsClient` (see its docstring) -- `build_gmail_service`
    itself is monkeypatched here (never called for real), so this test
    never touches OAuth/network, only verifies the wiring."""

    def test_wraps_the_service_returned_by_build_gmail_service(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        fake_service = _FakeGmailService({"id": "draft-via-build-gmail-service"})
        received_kwargs: dict[str, Any] = {}

        def _fake_build_gmail_service(
            *, credentials_path: Path | str | None, token_path: Path | str | None
        ) -> _FakeGmailService:
            received_kwargs["credentials_path"] = credentials_path
            received_kwargs["token_path"] = token_path
            return fake_service

        monkeypatch.setattr(
            gmail_draft_repository_module, "build_gmail_service", _fake_build_gmail_service
        )
        _write_fake_cv(tmp_path)

        repository = GmailDraftRepository(
            base_dir=tmp_path,
            credentials_path=tmp_path / "credentials.json",
            token_path=tmp_path / "token.json",
        )
        draft_id = repository.create_draft(
            to=EmailAddress("jobs@example.com"),
            subject="Subject",
            body="Body",
            cv_path="cvs/java/cv.pdf",
        )

        assert draft_id == "draft-via-build-gmail-service"
        assert received_kwargs == {
            "credentials_path": tmp_path / "credentials.json",
            "token_path": tmp_path / "token.json",
        }
        assert fake_service.drafts_resource.create_calls  # actually routed through the adapter
