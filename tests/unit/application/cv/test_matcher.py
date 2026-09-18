"""Unit tests for `app.application.cv.matcher.CVMatcher`.

Usa un catálogo en memoria (`_FakeCVCatalog`) que satisface el `Protocol`
`CVCatalog` -- ningún test acá toca el filesystem ni `config/cvs.yaml` real,
así que estos tests no se rompen si alguien edita el catálogo real más
adelante (mismo criterio que pide `docs/agents/AGENTS.md` sección 16: los
unit tests no dependen de I/O externo).
"""

from __future__ import annotations

import pytest

from app.application.cv.cv_profile import CVProfile
from app.application.cv.matcher import CVMatcher, CVMatchResult

pytestmark = pytest.mark.unit


class _FakeCVCatalog:
    """In-memory `CVCatalog` (satisface el Protocol por structural typing)."""

    def __init__(self, profiles: list[CVProfile]) -> None:
        self._profiles = profiles

    def list_cvs(self) -> list[CVProfile]:
        return list(self._profiles)

    def get_summary(self, cv_id: str) -> str:
        for profile in self._profiles:
            if profile.id == cv_id:
                return profile.summary
        raise KeyError(cv_id)


_JAVA_PROFILE = CVProfile(
    id="java",
    file="cvs/java/william-java.pdf.placeholder",
    skills=("Java", "Spring Boot", "Kafka", "Microservices"),
    summary="Java backend engineer summary.",
)
_PYTHON_PROFILE = CVProfile(
    id="python",
    file="cvs/python/william-python.pdf.placeholder",
    skills=("Python", "FastAPI", "Django"),
    summary="Python backend engineer summary.",
)


def _matcher(*profiles: CVProfile) -> CVMatcher:
    return CVMatcher(_FakeCVCatalog(list(profiles)))


class TestCVMatcherDeterministicSelection:
    """Caso explícito pedido por ROADMAP.md Fase 4, punto 6."""

    def test_selects_java_cv_for_java_skills_not_python(self) -> None:
        matcher = _matcher(_JAVA_PROFILE, _PYTHON_PROFILE)

        result = matcher.match(["Java", "Spring Boot"])

        assert result.recommended_cv == "java"
        assert result.matching_skills == ["Java", "Spring Boot"]
        assert result.missing_skills == []
        assert result.confidence == pytest.approx(1.0)

    def test_selects_python_cv_for_python_skills_not_java(self) -> None:
        matcher = _matcher(_JAVA_PROFILE, _PYTHON_PROFILE)

        result = matcher.match(["Python", "Django"])

        assert result.recommended_cv == "python"
        assert "Python" in result.matching_skills
        assert "Django" in result.matching_skills


class TestCVMatcherSkillNormalization:
    def test_matches_regardless_of_case(self) -> None:
        matcher = _matcher(_JAVA_PROFILE, _PYTHON_PROFILE)

        result = matcher.match(["java", "SPRING boot"])

        assert result.recommended_cv == "java"
        assert result.confidence == pytest.approx(1.0)

    def test_matches_node_alias_against_nodejs_skill(self) -> None:
        frontend = CVProfile(
            id="frontend",
            file="cvs/frontend/william-fullstack.pdf.placeholder",
            skills=("JavaScript", "Node.js", "React"),
            summary="Full stack summary.",
        )
        matcher = _matcher(frontend)

        result = matcher.match(["JS", "Node", "React"])

        assert result.recommended_cv == "frontend"
        assert set(result.matching_skills) == {"JavaScript", "Node.js", "React"}
        assert result.missing_skills == []


class TestCVMatcherTieBreak:
    def test_breaks_ties_by_catalog_order(self) -> None:
        # Ambos CVs comparten exactamente una skill ("Docker") con el job y
        # ninguna otra -- empate 1 a 1. El orden pasado al catálogo decide.
        first = CVProfile(
            id="first",
            file="cvs/first/cv.pdf.placeholder",
            skills=("Docker",),
            summary="First summary.",
        )
        second = CVProfile(
            id="second",
            file="cvs/second/cv.pdf.placeholder",
            skills=("Docker",),
            summary="Second summary.",
        )

        result_first_wins = _matcher(first, second).match(["Docker"])
        result_second_wins = _matcher(second, first).match(["Docker"])

        assert result_first_wins.recommended_cv == "first"
        assert result_second_wins.recommended_cv == "second"


class TestCVMatcherNoReasonableMatch:
    def test_returns_no_recommendation_when_nothing_matches(self) -> None:
        matcher = _matcher(_JAVA_PROFILE, _PYTHON_PROFILE)

        result = matcher.match(["Elixir", "Phoenix"])

        assert result == CVMatchResult(
            recommended_cv=None,
            matching_skills=[],
            missing_skills=["Elixir", "Phoenix"],
            confidence=0.0,
        )

    def test_returns_no_recommendation_for_empty_job_skills(self) -> None:
        matcher = _matcher(_JAVA_PROFILE, _PYTHON_PROFILE)

        result = matcher.match([])

        assert result == CVMatchResult(
            recommended_cv=None, matching_skills=[], missing_skills=[], confidence=0.0
        )

    def test_empty_catalog_never_forces_a_recommendation(self) -> None:
        matcher = _matcher()

        result = matcher.match(["Java"])

        assert result.recommended_cv is None
        assert result.confidence == 0.0


class TestCVMatcherConfidenceFormula:
    def test_confidence_is_matching_over_distinct_job_skills(self) -> None:
        matcher = _matcher(_JAVA_PROFILE, _PYTHON_PROFILE)

        # 2 de 3 skills distintas del job matchean el CV java (Kafka no).
        result = matcher.match(["Java", "Spring Boot", "AWS"])

        assert result.recommended_cv == "java"
        assert result.missing_skills == ["AWS"]
        assert result.confidence == pytest.approx(2 / 3)

    def test_duplicate_job_skills_do_not_inflate_denominator(self) -> None:
        matcher = _matcher(_JAVA_PROFILE, _PYTHON_PROFILE)

        result = matcher.match(["Java", "java", "Java "])

        assert result.recommended_cv == "java"
        assert result.confidence == pytest.approx(1.0)


class TestCVMatcherIgnoresUnusableSkillStrings:
    """`match()` descarta, sin contarlas ni en el numerador ni en el
    denominador, dos formas distintas de skill "inutilizable": (a) un
    string vacío/solo-espacios (nunca llega a `normalize_skill`), y (b) un
    string no vacío que normaliza a `""` porque solo tiene separadores
    (p. ej. `"---"`). Ver los dos `continue` documentados en el paso 1 del
    docstring de `match()`."""

    def test_blank_and_whitespace_only_skills_are_ignored(self) -> None:
        matcher = _matcher(_JAVA_PROFILE, _PYTHON_PROFILE)

        result = matcher.match(["Java", "", "   ", "Spring Boot"])

        assert result.recommended_cv == "java"
        assert result.confidence == pytest.approx(1.0)
        assert result.missing_skills == []

    def test_separator_only_skill_normalizing_to_empty_is_ignored(self) -> None:
        matcher = _matcher(_JAVA_PROFILE, _PYTHON_PROFILE)

        result = matcher.match(["Java", "---", "..."])

        assert result.recommended_cv == "java"
        assert result.confidence == pytest.approx(1.0)
        assert result.missing_skills == []
