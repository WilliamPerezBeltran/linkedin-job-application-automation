"""`JobSummaryResponse`: API response DTO for a single `Job` in list views
(`GET /api/jobs`, Fase 6 `ROADMAP.md`).

Deliberadamente separado de la entidad de dominio `Job`/`JobAnalysis`
(`ENGINEERING_STANDARDS.md` — nunca exponer una entidad de dominio
directamente por el boundary HTTP): este modelo Pydantic es el contrato
estable que viaja hacia el cliente de la API, mientras que `Job`/`JobAnalysis`
son detalles internos que pueden cambiar de forma independiente. Mismo
criterio ya aplicado por `ScrapeResultResponse`
(`app/application/dto/scrape_result.py`).

Los campos derivados de `JobAnalysis` (`job_type`, `skills`,
`recommended_cv`) son opcionales: un `Job` recién scrapeado (`SCRAPED`) o en
`ANALYZED`/`NOT_RELEVANT` sin oferta relevante todavía no tiene ninguna
`JobAnalysis` asociada -- quien construye este DTO
(`app/presentation/api/routes/jobs.py`) resuelve la `JobAnalysis` por
separado (`JobAnalysisRepository.get_by_job_id`) y la pasa como `None`
cuando no existe.

`JobDetailResponse` (`app/application/dto/job_detail_response.py`) extiende
este modelo por herencia (todo lo de aquí más los campos de detalle) — evita
duplicar la declaración de estos siete campos base.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.domain.value_objects.job_status import JobStatus


class JobSummaryResponse(BaseModel):
    id: str = Field(..., description="Job id (UUID as string).")
    author: str = Field(..., description="Author of the LinkedIn post, as scraped.")
    url: str = Field(..., description="URL of the original LinkedIn post.")
    status: JobStatus
    published_at: datetime | None = Field(
        default=None, description="Publish timestamp reported by LinkedIn, if available."
    )
    scraped_at: datetime
    job_type: str | None = Field(
        default=None,
        description="Category detected by the Job Analyzer. None if no JobAnalysis exists yet.",
    )
    skills: list[str] = Field(
        default_factory=list,
        description="Skills detected by the Job Analyzer. Empty if no JobAnalysis exists yet.",
    )
    recommended_cv: str | None = Field(
        default=None,
        description="CV catalog id recommended by the CV Matcher. None if not selected yet.",
    )
