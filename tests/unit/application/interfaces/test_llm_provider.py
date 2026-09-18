"""Structural-typing check for `LLMProvider`.

No hay todavía ningún use case (`AnalyzeJobPost`/`GenerateApplicationEmail`)
que consuma `LLMProvider` — ese contrato se fija en esta ronda, se consume
en una ronda siguiente (ver ADR-005). Este test solo verifica que un objeto
que implementa ambos métodos del Protocol (mismo patrón que
`FakeFeedCollector` en `tests/unit/application/use_cases/
test_collect_feed_posts.py`) puede asignarse a una variable tipada como
`LLMProvider` y usarse polimórficamente, sin heredar de ninguna clase base —
la garantía real de "no hereda de nada, cumple el contrato por forma" la da
`mypy --strict`; este test cubre el uso en tiempo de ejecución.
"""

from __future__ import annotations

import pytest

from app.application.dto.email_context import EmailContext
from app.application.dto.generated_email import GeneratedEmail
from app.application.dto.job_analysis_result import JobAnalysisResult
from app.application.interfaces.llm_provider import LLMProvider

pytestmark = pytest.mark.unit


class FakeLLMProvider:
    """In-memory `LLMProvider`: returns fixed DTOs, never calls a real provider."""

    def analyze_job(self, content: str) -> JobAnalysisResult:
        return JobAnalysisResult(
            is_job=True,
            job_type="Python",
            seniority="Senior",
            skills=("Python", "FastAPI"),
            languages=("Python",),
            frameworks=("FastAPI",),
            cloud=(),
            ai_related=False,
            email_addresses=("recruiter@example.com",),
            confidence=0.9,
        )

    def generate_email(self, context: EmailContext) -> GeneratedEmail:
        return GeneratedEmail(
            subject=f"Application for {context.job_type} role",
            body=f"Hi {context.author}, ...",
        )


class TestFakeLLMProviderSatisfiesTheProtocol:
    def test_can_be_used_polymorphically_as_llm_provider(self) -> None:
        provider: LLMProvider = FakeLLMProvider()

        analysis = provider.analyze_job("We are hiring a Senior Python Engineer...")

        assert isinstance(analysis, JobAnalysisResult)
        assert analysis.is_job is True
        assert analysis.job_type == "Python"

    def test_generate_email_returns_a_generated_email(self) -> None:
        provider: LLMProvider = FakeLLMProvider()
        context = EmailContext(
            job_type="Python",
            seniority="Senior",
            skills=("Python",),
            languages=("Python",),
            frameworks=("FastAPI",),
            author="Jane Recruiter",
            job_content="We are hiring a Senior Python Engineer...",
            cv_summary="Senior backend engineer, 5y Python/FastAPI, AWS.",
        )

        email = provider.generate_email(context)

        assert isinstance(email, GeneratedEmail)
        assert "Python" in email.subject
        assert "Jane Recruiter" in email.body
