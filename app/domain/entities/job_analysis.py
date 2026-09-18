"""`JobAnalysis` entity: LLM analysis result for a `Job`, progressively
enriched as it moves through the pipeline (Fase 3 Analyzer -> Fase 4 CV
Matcher -> Fase 5 Email Generator).

Campos según el modelo de datos de `CLAUDE.md` (tabla `job_analysis`):
`job_id, job_type, seniority, skills, languages, frameworks, cloud,
ai_related, match_score, recommended_cv, generated_email, subject`.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.domain.exceptions.invalid_domain_value_error import InvalidDomainValueError
from app.domain.value_objects.job_id import JobId


class JobAnalysis:
    """Result of analyzing a `Job` with an LLM, enriched over the pipeline.

    Invariante de negocio modelada aquí: no se puede registrar un email
    generado (Fase 5) antes de tener un CV recomendado (Fase 4) — refleja
    el orden real del pipeline descrito en `CLAUDE.md`
    (`Job Analyzer -> CV Matcher -> Email Generator`) y evita que un caller
    externo persista un `JobAnalysis` en un estado inconsistente.
    """

    def __init__(
        self,
        *,
        job_id: JobId,
        job_type: str,
        seniority: str | None,
        skills: Sequence[str],
        languages: Sequence[str],
        frameworks: Sequence[str],
        cloud: Sequence[str],
        ai_related: bool,
        match_score: float | None = None,
        recommended_cv: str | None = None,
        generated_email: str | None = None,
        subject: str | None = None,
    ) -> None:
        _require_non_empty("job_type", job_type)
        _validate_match_score(match_score)
        if generated_email is not None and recommended_cv is None:
            raise InvalidDomainValueError(
                field_name="generated_email",
                reason="cannot be set before recommended_cv (CV Matcher must run first)",
            )

        self._job_id = job_id
        self._job_type = job_type
        self._seniority = seniority
        self._skills = tuple(skills)
        self._languages = tuple(languages)
        self._frameworks = tuple(frameworks)
        self._cloud = tuple(cloud)
        self._ai_related = ai_related
        self._match_score = match_score
        self._recommended_cv = recommended_cv
        self._generated_email = generated_email
        self._subject = subject

    @property
    def job_id(self) -> JobId:
        return self._job_id

    @property
    def job_type(self) -> str:
        return self._job_type

    @property
    def seniority(self) -> str | None:
        return self._seniority

    @property
    def skills(self) -> tuple[str, ...]:
        return self._skills

    @property
    def languages(self) -> tuple[str, ...]:
        return self._languages

    @property
    def frameworks(self) -> tuple[str, ...]:
        return self._frameworks

    @property
    def cloud(self) -> tuple[str, ...]:
        return self._cloud

    @property
    def ai_related(self) -> bool:
        return self._ai_related

    @property
    def match_score(self) -> float | None:
        return self._match_score

    @property
    def recommended_cv(self) -> str | None:
        return self._recommended_cv

    @property
    def generated_email(self) -> str | None:
        return self._generated_email

    @property
    def subject(self) -> str | None:
        return self._subject

    def record_cv_recommendation(self, *, recommended_cv: str, match_score: float | None) -> None:
        """Fase 4 (CV Matcher): registra el CV elegido y su score de match."""
        _require_non_empty("recommended_cv", recommended_cv)
        _validate_match_score(match_score)
        self._recommended_cv = recommended_cv
        self._match_score = match_score

    def record_generated_email(self, *, subject: str, body: str) -> None:
        """Fase 5 (Email Generator): registra subject/body generados.

        Requiere que `record_cv_recommendation` ya se haya ejecutado —
        mismo invariante que en el constructor, aplicado a la actualización
        in-place de una instancia ya existente.
        """
        if self._recommended_cv is None:
            raise InvalidDomainValueError(
                field_name="generated_email",
                reason="cannot be set before recommended_cv (CV Matcher must run first)",
            )
        _require_non_empty("subject", subject)
        _require_non_empty("body", body)
        self._subject = subject
        self._generated_email = body

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, JobAnalysis):
            return NotImplemented
        return self._job_id == other._job_id

    def __hash__(self) -> int:
        return hash(self._job_id)


def _require_non_empty(field_name: str, value: str) -> None:
    if not value or not value.strip():
        raise InvalidDomainValueError(field_name=field_name, reason="must not be empty")


def _validate_match_score(match_score: float | None) -> None:
    if match_score is not None and not (0.0 <= match_score <= 1.0):
        raise InvalidDomainValueError(
            field_name="match_score", reason="must be between 0.0 and 1.0"
        )
