"""Unit tests for the stateless-composition dependency providers in
`app/presentation/api/dependencies.py`.

Cubre únicamente `get_cv_matcher`/`get_application_repository`: son pura
composición de objetos ya construidos por quien llama (`CVMatcher(cv_catalog)`,
`SQLAlchemyApplicationRepository(session)`), sin ningún I/O propio -- se
llaman acá directamente (sin pasar por FastAPI `Depends`) igual que se haría
con cualquier función normal, pasándoles un doble de prueba.

`get_llm_provider`/`get_email_draft_repository` (líneas 138 y 179 del módulo
bajo test) **no** se cubren acá a propósito: ambas construyen infraestructura
real sin ningún punto de inyección (`AnthropicProvider()`/
`GmailDraftRepository()` sin argumentos) -- la primera instanciaría un
`anthropic.Anthropic` real (aunque no llame a la red, es exactamente lo que
`tests/unit/infrastructure/llm/test_anthropic_provider.py` documenta evitar
en su propio docstring), y la segunda dispararía el flujo real de
`gmail_client.build_gmail_service` (lectura de `credentials.json`/OAuth) --
gap aceptado, ver el reporte de la tarea de cobertura para el razonamiento
completo. Todos los endpoints que sí usan esas dos dependencias
(`test_jobs.py`) las overridean vía `app.dependency_overrides`, nunca
invocan el proveedor real.
"""

from __future__ import annotations

import pytest

from app.application.cv.cv_profile import CVProfile
from app.domain.repositories.application_repository import ApplicationRepository
from app.infrastructure.database.repositories.sqlalchemy_application_repository import (
    SQLAlchemyApplicationRepository,
)
from app.presentation.api.dependencies import get_application_repository, get_cv_matcher

pytestmark = pytest.mark.unit


class _FakeCVCatalog:
    def __init__(self) -> None:
        self._profiles = [
            CVProfile(
                id="java",
                file="cvs/java/william-java.pdf",
                skills=("Java", "Spring Boot"),
                summary="Java backend engineer.",
            )
        ]

    def list_cvs(self) -> list[CVProfile]:
        return list(self._profiles)

    def get_summary(self, cv_id: str) -> str:
        return self._profiles[0].summary


class TestGetCvMatcher:
    def test_returns_a_matcher_bound_to_the_given_catalog(self) -> None:
        catalog = _FakeCVCatalog()

        matcher = get_cv_matcher(catalog)
        result = matcher.match(["Java", "Spring Boot"])

        assert result.recommended_cv == "java"


class TestGetApplicationRepository:
    def test_returns_a_repository_bound_to_the_given_session(self) -> None:
        from sqlalchemy.orm import Session

        session = Session()
        try:
            repository: ApplicationRepository = get_application_repository(session)

            assert isinstance(repository, SQLAlchemyApplicationRepository)
        finally:
            session.close()
