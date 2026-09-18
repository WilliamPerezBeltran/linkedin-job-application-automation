"""Unit tests for `app.application.services.candidate_filter.is_candidate_job_post`.

Función pura, sin I/O — no requiere mocks ni fixtures. Reubicado desde
`tests/unit/infrastructure/llm/` junto con el módulo que testea (ver
docstring de `app/application/services/candidate_filter.py` para el porqué).
"""

from __future__ import annotations

import pytest

from app.application.services.candidate_filter import is_candidate_job_post

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "content",
    [
        "We are hiring a Senior Python Developer! Remote, apply now with your CV.",
        "Buscamos desarrollador Java Senior con Spring Boot, modalidad remota. Postula ya.",
        "Exciting AI/ML Engineer opportunity, join our team working on deep learning models.",
        "Full Stack JavaScript/Node developer wanted, hybrid role in our engineering team.",
    ],
)
def test_is_candidate_job_post_accepts_clearly_tech_job_posts(content: str) -> None:
    assert is_candidate_job_post(content) is True


def test_is_candidate_job_post_rejects_short_irrelevant_post() -> None:
    assert is_candidate_job_post("Happy birthday!") is False


def test_is_candidate_job_post_rejects_content_shorter_than_minimum_length() -> None:
    assert is_candidate_job_post("short") is False


def test_is_candidate_job_post_rejects_long_post_without_any_keyword() -> None:
    content = (
        "Hoy fue un día hermoso para caminar por el parque junto al río, "
        "disfrutando del sol y charlando de música y películas con amigos "
        "de toda la vida, sin ninguna prisa ni compromiso pendiente."
    )
    assert len(content) >= 40

    assert is_candidate_job_post(content) is False
