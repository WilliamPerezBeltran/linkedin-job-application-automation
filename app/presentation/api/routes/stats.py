"""`GET /api/stats/dashboard`: Fase 8 (`ROADMAP.md`) -- aggregated stats
consumed by `frontend-agent`'s review/tracking dashboard ("jobs by status",
"applications sent by week", "applications sent by category").

De solo lectura y agregación: no muta ninguna entidad ni abre una
transacción de escritura -- reusa las mismas dependencias `Depends` ya
existentes (`get_job_repository`, `get_application_repository`,
`get_job_analysis_repository`, `app/presentation/api/dependencies.py`), cada
una atada a la `Session` de request vía `get_db_session`, pero este handler
nunca llama `save()` sobre nada.

**Decisión de escala (`backend-engineer`, documentada explícitamente porque
no es YAGNI trivial):** `jobs_by_status` se calcula iterando los 9 valores de
`JobStatus` y llamando `JobRepository.list_by_status(status, limit=10_000)`
para cada uno, en vez de agregar un método `count_by_status` nuevo al
`Protocol` de dominio (`app/domain/repositories/job_repository.py`). Esto es
`O(9 * N)` filas materializadas en memoria como entidades `Job` completas
solo para contar `len(...)` -- claramente no es la forma más eficiente de
contar filas (un `SELECT COUNT(*) ... GROUP BY status` sería trivial de
implementar en `SQLAlchemyJobRepository`), pero introducir un método de
agregación nuevo en el `Protocol` de dominio, usado por un único caller, para
una app local de un solo usuario cuyo volumen esperado de `Job`s está muy
por debajo de `10_000` filas totales, sería anticiparse a una necesidad que
no existe hoy (YAGNI, `docs/agents/AGENTS.md` principio 9) -- mismo criterio
que ya aplicó `ApplicationRepository.list_by_status` al no agregar
`created_at` "por si acaso". Límite explícito de esta decisión: si el volumen
de `Job`s/`Application`s crece sustancialmente (mucho más allá de lo que un
único usuario puede generar navegando su propio feed de LinkedIn), este es
el punto exacto donde agregar `count_by_status(status) -> int` a ambos
Protocols (dominio) e implementarlo con `SELECT COUNT(*)` (infraestructura)
deja de ser prematuro y se vuelve la elección correcta. Mismo límite
(`10_000`, `_MAX_ROWS_FOR_AGGREGATION`) se reusa para
`ApplicationRepository.list_by_status(ApplicationStatus.SENT, ...)`, por la
misma razón.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Sequence

from fastapi import APIRouter, Depends

from app.application.dto.dashboard_stats_response import (
    CategorySentCount,
    DashboardStatsResponse,
    WeeklySentCount,
)
from app.domain.entities.application import Application
from app.domain.repositories.application_repository import ApplicationRepository
from app.domain.repositories.job_analysis_repository import JobAnalysisRepository
from app.domain.repositories.job_repository import JobRepository
from app.domain.value_objects.application_status import ApplicationStatus
from app.domain.value_objects.job_status import JobStatus
from app.presentation.api.dependencies import (
    get_application_repository,
    get_job_analysis_repository,
    get_job_repository,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/stats", tags=["stats"])

# Ver el docstring del módulo -- cota práctica para "traer todo" en una app
# local de un solo usuario, sin agregar un método de conteo dedicado al
# dominio hoy.
_MAX_ROWS_FOR_AGGREGATION = 10_000

_UNKNOWN_CATEGORY = "unknown"


def _jobs_by_status(job_repository: JobRepository) -> dict[str, int]:
    """Counts Jobs per `JobStatus`, including statuses with zero Jobs.

    Ver el docstring del módulo para el razonamiento completo de esta
    implementación (`list_by_status` + `len`, no un `count_by_status`
    dedicado).
    """
    return {
        job_status.value: len(
            job_repository.list_by_status(job_status, limit=_MAX_ROWS_FOR_AGGREGATION)
        )
        for job_status in JobStatus
    }


def _applications_sent_by_week(sent_applications: Sequence[Application]) -> list[WeeklySentCount]:
    """Groups `sent_applications` by the ISO week of `sent_at`, chronologically.

    `sent_at` nunca debería ser `None` para una `Application` en `SENT` --
    invariante garantizada por `Application.__init__`
    (`_require_status_sent_at_consistency`,
    `app/domain/entities/application.py`) -- pero, igual que
    `_applications_sent_by_category` se defiende de una `JobAnalysis`
    faltante, se aplica acá el mismo criterio defensivo explícito (chequeo +
    warning logueado + `continue`) en vez de un `assert` (que además
    desaparece si el proceso corre con `-O`/`PYTHONOPTIMIZE`, ver hallazgo de
    `code-reviewer`): un dato de trazabilidad corrupto no debería tirar abajo
    el endpoint completo del dashboard.

    Orden cronológico: el formato `'%G-W%V'` (año ISO + semana ISO,
    zero-padded a 2 dígitos) ordena correctamente como texto plano -- no
    hace falta parsear de vuelta a fecha para ordenar.
    """
    counts: Counter[str] = Counter()
    for application in sent_applications:
        if application.sent_at is None:
            logger.warning(
                "stats.dashboard.sent_application_missing_sent_at application_id=%s",
                application.id,
            )
            continue
        week = application.sent_at.strftime("%G-W%V")
        counts[week] += 1
    return [WeeklySentCount(week=week, count=count) for week, count in sorted(counts.items())]


def _applications_sent_by_category(
    sent_applications: Sequence[Application], job_analysis_repository: JobAnalysisRepository
) -> list[CategorySentCount]:
    """Groups `sent_applications` by `JobAnalysis.job_type`.

    Defensa explícita pedida por esta fase: si no hay `JobAnalysis` para
    `application.job_id` -- no debería pasar en un pipeline sano, ya que
    `CreateGmailDraft.execute_one` exige una `JobAnalysis` completa antes de
    crear cualquier `Application` (ver su docstring) -- se cuenta bajo
    `"unknown"` en vez de romper el endpoint completo, con un warning
    logueado para poder investigarlo.
    """
    counts: Counter[str] = Counter()
    for application in sent_applications:
        job_analysis = job_analysis_repository.get_by_job_id(application.job_id)
        if job_analysis is None:
            logger.warning(
                "stats.dashboard.missing_job_analysis application_id=%s job_id=%s",
                application.id,
                application.job_id,
            )
            counts[_UNKNOWN_CATEGORY] += 1
            continue
        counts[job_analysis.job_type] += 1
    return [
        CategorySentCount(category=category, count=count)
        for category, count in sorted(counts.items())
    ]


@router.get("/dashboard", response_model=DashboardStatsResponse)
def get_dashboard_stats(
    job_repository: JobRepository = Depends(get_job_repository),
    application_repository: ApplicationRepository = Depends(get_application_repository),
    job_analysis_repository: JobAnalysisRepository = Depends(get_job_analysis_repository),
) -> DashboardStatsResponse:
    sent_applications = application_repository.list_by_status(
        ApplicationStatus.SENT, limit=_MAX_ROWS_FOR_AGGREGATION
    )
    return DashboardStatsResponse(
        jobs_by_status=_jobs_by_status(job_repository),
        applications_sent_total=len(sent_applications),
        applications_sent_by_week=_applications_sent_by_week(sent_applications),
        applications_sent_by_category=_applications_sent_by_category(
            sent_applications, job_analysis_repository
        ),
    )
