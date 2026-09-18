"""Composition root.

Este módulo es el punto de entrada de la aplicación FastAPI. Expone un
health check, el router de `POST /scrape` (Fase 2,
`app/presentation/api/routes/scrape.py`) que invoca el use case
`CollectFeedPosts` a demanda, desde Fase 6 el router de `/api/jobs/**`
(`app/presentation/api/routes/jobs.py`) con la API de revisión manual
(listar/ver jobs, ignorar, analizar, generar email, crear draft de Gmail), y
desde Fase 8 los routers de `/api/applications/**`
(`app/presentation/api/routes/applications.py`, confirmación manual de envío)
y `/api/stats/**` (`app/presentation/api/routes/stats.py`, agregados para el
dashboard de trazabilidad). La composición real de dependencias (routers,
use cases, repositorios) es ownership de `backend-engineer` (ver
docs/architecture/clean-architecture-skeleton.md).
"""

from fastapi import FastAPI

from app.presentation.api.routes.applications import router as applications_router
from app.presentation.api.routes.jobs import router as jobs_router
from app.presentation.api.routes.scrape import router as scrape_router
from app.presentation.api.routes.stats import router as stats_router

app = FastAPI(
    title="linkedin-job-application-automation",
    description=(
        "Automated workflow for collecting LinkedIn job posts, analyzing "
        "opportunities with AI, generating application emails, and "
        "selecting the appropriate CV."
    ),
    version="0.0.1",
)

app.include_router(scrape_router)
app.include_router(jobs_router)
app.include_router(applications_router)
app.include_router(stats_router)


@app.get("/health", tags=["health"])
def health_check() -> dict[str, str]:
    """Liveness check. No depende de la base de datos ni de servicios externos."""
    return {"status": "ok"}
