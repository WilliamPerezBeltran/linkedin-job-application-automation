"""Unit tests for `app.application.dto.application_response.build_gmail_url`.

`build_gmail_url` es una función pura sin ninguna dependencia externa --
estos tests no pasan por FastAPI ni por ningún repositorio, ver el
docstring del módulo bajo test para el contrato completo (incluyendo por
qué `gmail_draft_id=None` es hoy inalcanzable vía la API real, pero se
maneja explícitamente de todos modos).
"""

from __future__ import annotations

import pytest

from app.application.dto.application_response import build_gmail_url

pytestmark = pytest.mark.unit


class TestBuildGmailUrl:
    def test_returns_none_when_gmail_draft_id_is_none(self) -> None:
        assert build_gmail_url(None) is None

    def test_builds_the_gmail_web_ui_url_for_a_draft_id(self) -> None:
        url = build_gmail_url("draft-abc123")

        assert url == "https://mail.google.com/mail/u/0/#drafts/draft-abc123"
