"""`JobDetailResponse`: API response DTO for `GET /api/jobs/{id}`
(Fase 6, `ROADMAP.md`), and reused as the response of
`POST /api/jobs/{id}/analyze` / `POST /api/jobs/{id}/generate-email`
(`app/presentation/api/routes/jobs.py`) since both endpoints leave the `Job`
in a state richer than what `JobSummaryResponse` alone can show.

Extiende `JobSummaryResponse` por herencia -- mismos siete campos base, más
el detalle completo de la `JobAnalysis` asociada (o valores vacíos/`None` si
todavía no existe).

`matching_skills`/`missing_skills` **no** tienen columna equivalente en
`JobAnalysis`/`job_analysis` (ver `app/domain/entities/job_analysis.py`):
se calculan al vuelo en `app/presentation/api/routes/jobs.py`, cruzando
`JobAnalysis.skills` contra las skills del `CVProfile` recomendado
(`CVCatalog.list_cvs()`), normalizadas con
`app.application.cv.skill_normalizer.normalize_skill` -- nunca persistidos.
Van vacíos si `recommended_cv` es `None` (CV Matcher no corrió todavía).
"""

from __future__ import annotations

from pydantic import Field

from app.application.dto.job_summary_response import JobSummaryResponse


class JobDetailResponse(JobSummaryResponse):
    seniority: str | None = Field(default=None, description="Seniority detected by the analyzer.")
    languages: list[str] = Field(default_factory=list)
    frameworks: list[str] = Field(default_factory=list)
    cloud: list[str] = Field(default_factory=list)
    ai_related: bool | None = Field(
        default=None, description="None if no JobAnalysis exists yet."
    )
    matching_skills: list[str] = Field(
        default_factory=list,
        description=(
            "Skills the recommended CV can demonstrate, as written in the CV catalog. "
            "Computed on the fly, never persisted — see module docstring."
        ),
    )
    missing_skills: list[str] = Field(
        default_factory=list,
        description=(
            "Job skills, as detected by the analyzer, not covered by the recommended CV. "
            "Computed on the fly, never persisted — see module docstring."
        ),
    )
    subject: str | None = Field(
        default=None, description="Generated email subject. None until EMAIL_GENERATED."
    )
    generated_email: str | None = Field(
        default=None, description="Generated email body. None until EMAIL_GENERATED."
    )
