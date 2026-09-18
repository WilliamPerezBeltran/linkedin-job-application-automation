"""Unit tests for `app.infrastructure.llm.anthropic_provider.AnthropicProvider`.

Ninguno de estos tests instancia `anthropic.Anthropic` real ni golpea la
red: todos inyectan un `client` de prueba en el constructor (que satisface
`_CompletionClient` por forma, ver el módulo bajo test) devolviendo texto
fijo o lanzando una excepción simulada.
"""

from __future__ import annotations

import anthropic
import pytest

from app.application.dto.email_context import EmailContext
from app.application.dto.generated_email import GeneratedEmail
from app.application.dto.job_analysis_result import JobAnalysisResult
from app.infrastructure.llm.anthropic_provider import AnthropicProvider, _AnthropicSDKClient
from app.infrastructure.llm.exceptions import LLMProviderError, LLMResponseValidationError

pytestmark = pytest.mark.unit


class _FakeCompletionClient:
    """Test double for `_CompletionClient`: returns a fixed string, never
    calls Anthropic."""

    def __init__(self, response_text: str) -> None:
        self._response_text = response_text

    def complete(self, *, system_prompt: str, user_content: str, model: str) -> str:
        return self._response_text


class _RaisingCompletionClient:
    """Test double simulating a failure of the underlying SDK/HTTP call."""

    def complete(self, *, system_prompt: str, user_content: str, model: str) -> str:
        raise RuntimeError("simulated network failure")


_VALID_RESPONSE = (
    '{"is_job": true, "job_category": "Python", "seniority": "Senior", '
    '"skills": ["Python", "FastAPI"], "languages": ["Python"], '
    '"frameworks": ["FastAPI"], "cloud": ["AWS"], "ai_related": false, '
    '"email_addresses": ["jobs@example.com"], "confidence": 0.9}'
)

_VALID_EMAIL_RESPONSE = (
    '{"subject": "Postulaci\\u00f3n - Backend Developer Senior", '
    '"body": "Estimados, me postulo a la posici\\u00f3n publicada. Saludos."}'
)


def _provider_returning(response_text: str) -> AnthropicProvider:
    return AnthropicProvider(client=_FakeCompletionClient(response_text))


def _sample_email_context(
    *, job_content: str = "We are hiring a Senior Python Engineer..."
) -> EmailContext:
    return EmailContext(
        job_type="Python",
        seniority="Senior",
        skills=("Python", "FastAPI"),
        languages=("Python",),
        frameworks=("FastAPI",),
        author="TechCorp",
        job_content=job_content,
        cv_summary="Backend developer with 5 years of experience in Python and FastAPI.",
    )


class TestAnalyzeJobHappyPath:
    def test_valid_json_response_is_parsed_and_translated_to_dto(self) -> None:
        provider = _provider_returning(_VALID_RESPONSE)

        result = provider.analyze_job("We are hiring a Senior Python Engineer...")

        assert isinstance(result, JobAnalysisResult)
        assert result.is_job is True
        # `job_category` (JSON crudo del proveedor) se traduce a `job_type`
        # (vocabulario del DTO/dominio) — ver ADR-005 sección 2.
        assert result.job_type == "Python"
        assert result.seniority == "Senior"
        assert result.skills == ("Python", "FastAPI")
        assert result.languages == ("Python",)
        assert result.frameworks == ("FastAPI",)
        assert result.cloud == ("AWS",)
        assert result.ai_related is False
        assert result.email_addresses == ("jobs@example.com",)
        assert result.confidence == 0.9

    def test_response_wrapped_in_markdown_fences_is_still_parsed(self) -> None:
        fenced_response = f"```json\n{_VALID_RESPONSE}\n```"
        provider = _provider_returning(fenced_response)

        result = provider.analyze_job("We are hiring a Senior Python Engineer...")

        assert result.job_type == "Python"

    def test_malformed_extracted_email_is_dropped_without_failing_validation(self) -> None:
        response = _VALID_RESPONSE.replace(
            '"email_addresses": ["jobs@example.com"]',
            '"email_addresses": ["not-an-email", "jobs@example.com"]',
        )
        provider = _provider_returning(response)

        result = provider.analyze_job("some job post")

        assert result.email_addresses == ("jobs@example.com",)


class TestAnalyzeJobValidationFailures:
    def test_category_outside_closed_list_raises_validation_error(self) -> None:
        invalid_category_response = _VALID_RESPONSE.replace('"Python"', '"Rust"', 1)
        provider = _provider_returning(invalid_category_response)

        with pytest.raises(LLMResponseValidationError):
            provider.analyze_job("some job post")

    def test_malformed_json_raises_validation_error(self) -> None:
        provider = _provider_returning("this is not valid json at all {")

        with pytest.raises(LLMResponseValidationError):
            provider.analyze_job("some job post")

    def test_confidence_out_of_range_raises_validation_error(self) -> None:
        out_of_range_response = _VALID_RESPONSE.replace('"confidence": 0.9', '"confidence": 1.5')
        provider = _provider_returning(out_of_range_response)

        with pytest.raises(LLMResponseValidationError):
            provider.analyze_job("some job post")

    def test_missing_required_field_raises_validation_error(self) -> None:
        response_without_confidence = _VALID_RESPONSE.replace(', "confidence": 0.9', "")
        provider = _provider_returning(response_without_confidence)

        with pytest.raises(LLMResponseValidationError):
            provider.analyze_job("some job post")


class TestAnalyzeJobProviderFailure:
    def test_sdk_exception_raises_llm_provider_error(self) -> None:
        provider = AnthropicProvider(client=_RaisingCompletionClient())

        with pytest.raises(LLMProviderError):
            provider.analyze_job("some job post")


class TestAnthropicProviderConfiguration:
    def test_missing_api_key_raises_llm_provider_error_without_touching_the_sdk(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        with pytest.raises(LLMProviderError):
            AnthropicProvider(api_key=None)


class TestAnalyzeJobDefensiveTruncation:
    def test_content_longer_than_max_input_chars_is_truncated_before_calling_the_client(
        self,
    ) -> None:
        seen_content: list[str] = []

        class _RecordingClient:
            def complete(self, *, system_prompt: str, user_content: str, model: str) -> str:
                seen_content.append(user_content)
                return _VALID_RESPONSE

        provider = AnthropicProvider(client=_RecordingClient(), max_input_chars=10)

        provider.analyze_job("x" * 1000)

        assert len(seen_content) == 1
        assert len(seen_content[0]) == 10


class TestGenerateEmailHappyPath:
    def test_valid_json_response_is_parsed_and_translated_to_dto(self) -> None:
        provider = AnthropicProvider(client=_FakeCompletionClient(_VALID_EMAIL_RESPONSE))

        result = provider.generate_email(_sample_email_context())

        assert isinstance(result, GeneratedEmail)
        assert result.subject == "Postulación - Backend Developer Senior"
        assert result.body == "Estimados, me postulo a la posición publicada. Saludos."

    def test_response_wrapped_in_markdown_fences_is_still_parsed(self) -> None:
        fenced_response = f"```json\n{_VALID_EMAIL_RESPONSE}\n```"
        provider = AnthropicProvider(client=_FakeCompletionClient(fenced_response))

        result = provider.generate_email(_sample_email_context())

        assert result.subject == "Postulación - Backend Developer Senior"


class TestGenerateEmailValidationFailures:
    def test_blank_subject_raises_validation_error(self) -> None:
        response = _VALID_EMAIL_RESPONSE.replace(
            '"Postulaci\\u00f3n - Backend Developer Senior"', '"   "'
        )
        provider = AnthropicProvider(client=_FakeCompletionClient(response))

        with pytest.raises(LLMResponseValidationError):
            provider.generate_email(_sample_email_context())

    def test_blank_body_raises_validation_error(self) -> None:
        response = _VALID_EMAIL_RESPONSE.replace(
            '"Estimados, me postulo a la posici\\u00f3n publicada. Saludos."', '""'
        )
        provider = AnthropicProvider(client=_FakeCompletionClient(response))

        with pytest.raises(LLMResponseValidationError):
            provider.generate_email(_sample_email_context())

    def test_malformed_json_raises_validation_error(self) -> None:
        provider = AnthropicProvider(client=_FakeCompletionClient("this is not json {"))

        with pytest.raises(LLMResponseValidationError):
            provider.generate_email(_sample_email_context())

    def test_missing_required_field_raises_validation_error(self) -> None:
        response_without_body = '{"subject": "Postulación"}'
        provider = AnthropicProvider(client=_FakeCompletionClient(response_without_body))

        with pytest.raises(LLMResponseValidationError):
            provider.generate_email(_sample_email_context())


class TestGenerateEmailProviderFailure:
    def test_sdk_exception_raises_llm_provider_error(self) -> None:
        provider = AnthropicProvider(client=_RaisingCompletionClient())

        with pytest.raises(LLMProviderError):
            provider.generate_email(_sample_email_context())


class TestGenerateEmailDefensiveTruncation:
    def test_job_content_longer_than_max_input_chars_is_truncated_before_calling_the_client(
        self,
    ) -> None:
        seen_content: list[str] = []

        class _RecordingClient:
            def complete(self, *, system_prompt: str, user_content: str, model: str) -> str:
                seen_content.append(user_content)
                return _VALID_EMAIL_RESPONSE

        provider = AnthropicProvider(client=_RecordingClient(), max_input_chars=10)

        provider.generate_email(_sample_email_context(job_content="x" * 1000))

        assert len(seen_content) == 1
        # El job_content truncado a 10 caracteres queda embebido en el
        # mensaje de usuario serializado (no todo el mensaje mide 10, a
        # diferencia de `analyze_job`, porque acá se agregan más campos
        # estructurados alrededor).
        assert "x" * 10 in seen_content[0]
        assert "x" * 11 not in seen_content[0]


class TestAnthropicProviderModelSelection:
    """Confirma que `analyze_job` y `generate_email` consultan al
    `_CompletionClient` con el `model` correspondiente a cada tarea
    (`TOKEN_OPTIMIZATION.md` §6: Analyzer barato/rápido, Email Generator
    intermedio/de mejor calidad de redacción) — ambos métodos comparten una
    única instancia de cliente inyectada, pero deben pasarle un `model`
    distinto por llamada.
    """

    def test_analyze_job_and_generate_email_use_different_configured_models(self) -> None:
        seen_models: list[str] = []

        class _ModelRecordingClient:
            def complete(self, *, system_prompt: str, user_content: str, model: str) -> str:
                seen_models.append(model)
                if user_content == "some job post":
                    return _VALID_RESPONSE
                return _VALID_EMAIL_RESPONSE

        provider = AnthropicProvider(
            client=_ModelRecordingClient(),
            model="analysis-model-x",
            email_model="email-model-y",
        )

        provider.analyze_job("some job post")
        provider.generate_email(_sample_email_context())

        assert seen_models == ["analysis-model-x", "email-model-y"]

    def test_default_models_differ_between_analyze_job_and_generate_email(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
        monkeypatch.delenv("ANTHROPIC_EMAIL_MODEL", raising=False)
        seen_models: list[str] = []

        class _ModelRecordingClient:
            def complete(self, *, system_prompt: str, user_content: str, model: str) -> str:
                seen_models.append(model)
                return _VALID_RESPONSE if len(seen_models) == 1 else _VALID_EMAIL_RESPONSE

        provider = AnthropicProvider(client=_ModelRecordingClient())

        provider.analyze_job("some job post")
        provider.generate_email(_sample_email_context())

        assert len(seen_models) == 2
        assert seen_models[0] != seen_models[1]


class _FakeAnthropicMessagesClient:
    """Stand-in for `anthropic.Anthropic(...).messages` -- records the
    exact kwargs passed to `.create(...)` and returns a fixed fake
    `Message` (just an object with a `.content` list), never a real
    `anthropic.Anthropic` client and never any network call."""

    def __init__(self, content_blocks: list[object]) -> None:
        self._content_blocks = content_blocks
        self.calls: list[dict[str, object]] = []

    def create(
        self, *, model: str, max_tokens: int, system: str, messages: list[object]
    ) -> object:
        self.calls.append(
            {"model": model, "max_tokens": max_tokens, "system": system, "messages": messages}
        )
        return _FakeAnthropicMessage(self._content_blocks)


class _FakeAnthropicMessage:
    def __init__(self, content_blocks: list[object]) -> None:
        self.content = content_blocks


class _FakeAnthropicSDKClient:
    """Stand-in for a real `anthropic.Anthropic` instance -- exposes only
    the `.messages` attribute `_AnthropicSDKClient` touches."""

    def __init__(self, content_blocks: list[object]) -> None:
        self.messages = _FakeAnthropicMessagesClient(content_blocks)


class TestAnthropicSDKClientAdapter:
    """`_AnthropicSDKClient` adapts a real `anthropic.Anthropic` client to
    `_CompletionClient` -- exercised here against a fake double (never a
    real `anthropic.Anthropic`, which would need a network-capable SDK
    client) to cover the actual `.messages.create(...)` call and the
    `TextBlock` concatenation/filtering logic."""

    def test_complete_calls_messages_create_with_expected_arguments(self) -> None:
        text_block = anthropic.types.TextBlock(type="text", text="Hello there")
        fake_client = _FakeAnthropicSDKClient([text_block])
        adapter = _AnthropicSDKClient(fake_client, max_output_tokens=500)  # type: ignore[arg-type]

        result = adapter.complete(
            system_prompt="You are a job analyzer.", user_content="a job post", model="claude-x"
        )

        assert result == "Hello there"
        assert fake_client.messages.calls == [
            {
                "model": "claude-x",
                "max_tokens": 500,
                "system": "You are a job analyzer.",
                "messages": [{"role": "user", "content": "a job post"}],
            }
        ]

    def test_complete_concatenates_multiple_text_blocks_in_order(self) -> None:
        blocks = [
            anthropic.types.TextBlock(type="text", text="Hello "),
            anthropic.types.TextBlock(type="text", text="world"),
        ]
        adapter = _AnthropicSDKClient(
            _FakeAnthropicSDKClient(blocks), max_output_tokens=500  # type: ignore[arg-type]
        )

        result = adapter.complete(system_prompt="sys", user_content="hi", model="claude-x")

        assert result == "Hello world"

    def test_complete_ignores_non_text_content_blocks(self) -> None:
        """Un `ThinkingBlock` (u otro tipo de bloque no textual) nunca debe
        colarse en el resultado concatenado -- `_AnthropicSDKClient.complete`
        filtra explícitamente por `isinstance(block, anthropic.types.TextBlock)`."""
        blocks: list[object] = [
            anthropic.types.ThinkingBlock(
                type="thinking", thinking="internal reasoning", signature="sig"
            ),
            anthropic.types.TextBlock(type="text", text="final answer"),
        ]
        adapter = _AnthropicSDKClient(
            _FakeAnthropicSDKClient(blocks), max_output_tokens=500  # type: ignore[arg-type]
        )

        result = adapter.complete(system_prompt="sys", user_content="hi", model="claude-x")

        assert result == "final answer"
