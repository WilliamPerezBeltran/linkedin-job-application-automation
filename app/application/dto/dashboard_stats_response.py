"""`DashboardStatsResponse`: API response DTO for `GET /api/stats/dashboard`
(Fase 8, `ROADMAP.md`, "Done cuando: se puede ver de un vistazo cuántas
ofertas están en cada estado y cuántas aplicaciones se han enviado").

Un único DTO agregando todo lo que `frontend-agent` necesita para el
tablero de histórico/trazabilidad de esta fase, para que el frontend no
tenga que hacer N llamados a la API ni reimplementar agregación (conteos,
agrupación por semana ISO, cruce `Application` -> `JobAnalysis.job_type`)
que ya vive del lado del servidor, con acceso directo a los repositorios.

Mismo criterio de "un DTO nuevo por endpoint agregador", ya usado por
`ApplicationResponse` (Fase 7) frente a `JobDetailResponse`: este recurso
(`stats/dashboard`) no es una entidad de dominio ni una vista de una sola
entidad -- es una vista agregada de `Job`+`Application`+`JobAnalysis`, así
que no tiene sentido modelarlo como extensión de ninguno de los DTOs
existentes.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class WeeklySentCount(BaseModel):
    """One bucket of `applications_sent_by_week`."""

    week: str = Field(
        ...,
        description=(
            "ISO week of Application.sent_at, format 'GGGG-Www' "
            "(datetime.strftime('%G-W%V')), e.g. '2026-W38'."
        ),
    )
    count: int = Field(..., description="Number of Applications SENT during this ISO week.")


class CategorySentCount(BaseModel):
    """One bucket of `applications_sent_by_category`."""

    category: str = Field(
        ...,
        description=(
            "JobAnalysis.job_type of the associated Job, or 'unknown' if no "
            "JobAnalysis was found for it (defensive fallback, should not "
            "happen in a healthy pipeline -- see app/presentation/api/routes/stats.py)."
        ),
    )
    count: int = Field(..., description="Number of Applications SENT for this category.")


class DashboardStatsResponse(BaseModel):
    jobs_by_status: dict[str, int] = Field(
        ...,
        description=(
            "Count of Jobs currently in each of the 9 JobStatus values, "
            "including entries with count 0 so the frontend never has to "
            "fill gaps itself."
        ),
    )
    applications_sent_total: int = Field(
        ..., description="Total number of Applications currently in SENT."
    )
    applications_sent_by_week: list[WeeklySentCount] = Field(
        default_factory=list,
        description="Applications SENT, grouped by ISO week, ordered chronologically.",
    )
    applications_sent_by_category: list[CategorySentCount] = Field(
        default_factory=list,
        description="Applications SENT, grouped by job category (JobAnalysis.job_type).",
    )
