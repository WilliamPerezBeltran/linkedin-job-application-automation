"""`POST /scrape`: runs `CollectFeedPosts` once, on demand.

Composition root para este endpoint (ver ROADMAP.md Fase 2 punto 6).

Límite transaccional (ver `app/infrastructure/database/session.py` y
`sqlalchemy_job_repository.py`): el scraping real con Playwright
(`feed_collector.collect()`) corre **fuera** de `session_scope()` — nunca se
mantiene una transacción de PostgreSQL abierta mientras Playwright navega el
feed. `session_scope()` solo envuelve `CollectFeedPosts.persist(raw_posts)`,
la parte que efectivamente toca `JobRepository`.

Traducción de excepciones a HTTP (nunca se silencian, ver
`docs/agents/AGENTS.md` sección 8):

- `LinkedInAuthenticationError` (no hay `storage_state.json` utilizable, o
  la sesión persistida expiró) -> `401 Unauthorized`. La resolución requiere
  que el usuario corra el login manual/controlado por separado
  (`app.infrastructure.linkedin.session.run_manual_login`), nunca una
  reautenticación automática dentro de este endpoint.
- Cualquier otro `LinkedInInfrastructureError` (checkpoint/CAPTCHA,
  navegación bloqueada fuera de whitelist, error de parsing del feed)
  -> `502 Bad Gateway`, logueado como error para diagnóstico — LinkedIn (o
  el propio scraping) falló de forma inesperada, no es un error del cliente
  de esta API.
- `IntegrityError` de SQLAlchemy al persistir (dos invocaciones concurrentes
  de `/scrape` intentando guardar el mismo `content_hash` en la ventana
  entre el chequeo de dedup y el `save()`) -> `409 Conflict`, logueado.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, status
from sqlalchemy.exc import IntegrityError

from app.application.dto.scrape_result import ScrapeResultResponse
from app.application.use_cases.collect_feed_posts import CollectFeedPosts
from app.infrastructure.database.repositories.sqlalchemy_job_repository import (
    SQLAlchemyJobRepository,
)
from app.infrastructure.database.session import session_scope
from app.infrastructure.linkedin.exceptions import (
    LinkedInAuthenticationError,
    LinkedInInfrastructureError,
)
from app.infrastructure.linkedin.linkedin_feed_collector import LinkedInFeedCollector

logger = logging.getLogger(__name__)

router = APIRouter(tags=["scrape"])


@router.post("/scrape", response_model=ScrapeResultResponse)
def scrape_feed() -> ScrapeResultResponse:
    feed_collector = LinkedInFeedCollector()

    try:
        raw_posts = feed_collector.collect()
    except LinkedInAuthenticationError as exc:
        logger.warning("scrape.linkedin_authentication_error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                "No authenticated LinkedIn session available. Run the manual "
                "login flow before scraping again."
            ),
        ) from exc
    except LinkedInInfrastructureError as exc:
        logger.error("scrape.linkedin_infrastructure_error: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="LinkedIn scraping failed. Check server logs for details.",
        ) from exc

    try:
        with session_scope() as session:
            job_repository = SQLAlchemyJobRepository(session)
            use_case = CollectFeedPosts(
                feed_collector=feed_collector, job_repository=job_repository
            )
            result = use_case.persist(raw_posts)
    except IntegrityError as exc:
        logger.error("scrape.duplicate_content_hash_race: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=("A concurrent scrape already persisted one of these posts. Retry the request."),
        ) from exc

    return ScrapeResultResponse(
        saved=result.saved,
        skipped_duplicates=result.skipped_duplicates,
        skipped_invalid=result.skipped_invalid,
    )
