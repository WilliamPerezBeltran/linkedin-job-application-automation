"""`ScrapeResultResponse`: API response DTO for `POST /scrape`.

Deliberadamente separado de `CollectFeedPostsResult`
(`app/application/use_cases/collect_feed_posts.py`): ese dataclass es un
detalle interno del use case, mientras que este modelo Pydantic es el
contrato estable que viaja por el boundary HTTP (`ENGINEERING_STANDARDS.md`
— nunca exponer directamente el resultado interno de un use case ni una
entidad de dominio). Para este endpoint ambos tienen los mismos campos hoy,
pero pueden divergir sin romper al cliente de la API si el use case cambia.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ScrapeResultResponse(BaseModel):
    saved: int = Field(..., description="New jobs persisted in this run.")
    skipped_duplicates: int = Field(
        ..., description="Posts discarded because their content_hash was already persisted."
    )
    skipped_invalid: int = Field(
        ...,
        description=(
            "Posts discarded because they violated a domain invariant "
            "(e.g. an empty url) — logged as a warning, never aborting the batch."
        ),
    )
