"""`RawFeedPost`: the DTO exchanged across the `FeedCollector` boundary.

Ver `docs/decisions/003-feed-collector-interface.md` para la decisión
completa. Resumen: `linkedin-agent` (`app/infrastructure/linkedin/**`)
nunca construye la entidad `Job` ni conoce sus reglas de validación —
`FeedCollector.collect()` devuelve una lista de `RawFeedPost`, y es el
use case `CollectFeedPosts` (`backend-engineer`,
`app/application/use_cases/collect_feed_posts.py`) quien los traduce a
`Job` vía `Job.create(...)`.

Deliberadamente un **plain data carrier** (dataclass congelado, sin
`__post_init__` con invariantes de negocio): la validación de "no vacío"
ya vive una sola vez en `Job.create()` (`app/domain/entities/job.py`).
Duplicarla aquí generaría dos fuentes de verdad para la misma regla.

Campos alineados con `ROADMAP.md` Fase 2 punto 3 (`feed.py` extrae
autor/texto/timestamp/URL) y punto 5 (`linkedin_feed_collector.py`
calcula `content_hash = sha256(normalized_content)`). No incluye `email`:
en Fase 2 el collector no extrae email del feed (`feed.py` solo extrae
autor/texto/timestamp/URL); la extracción de email es responsabilidad del
Job Analyzer LLM en Fase 3 (`CLAUDE.md`, pipeline: "Job Analyzer (LLM) →
... email"). `CollectFeedPosts` pasa `email=None` a `Job.create(...)` para
todo `Job` recién scrapeado.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class RawFeedPost:
    """A single LinkedIn feed post exactly as extracted and normalized by
    infrastructure (`feed.py` + `parser.py`), before it becomes a `Job`.

    - `author`: nombre del autor del post, tal como aparece en el feed.
    - `content`: texto del post ya normalizado por `parser.py` (HTML
      limpio, sin markup) — es el mismo texto sobre el que se calculó
      `content_hash`.
    - `content_hash`: `sha256` hexdigest del `content` normalizado,
      calculado por `linkedin_feed_collector.py` (infraestructura decide
      el algoritmo; el dominio solo exige que no esté vacío, ver
      `Job.create` docstring).
    - `url`: URL del post dentro del feed (nunca se navega a ella).
    - `published_at`: timestamp del post si LinkedIn lo expone de forma
      resoluble a `datetime` (LinkedIn suele mostrar tiempos relativos
      como "2h"/"3d"); `None` si no se puede resolver de forma confiable
      — decisión de `linkedin-agent` en su propia capa, no de este DTO.
    """

    author: str
    content: str
    content_hash: str
    url: str
    published_at: datetime | None
