"""Port towards LinkedIn feed scraping, consumed by `CollectFeedPosts`.

Ver `docs/decisions/003-feed-collector-interface.md` para la decisión
completa (ubicación, firma, tipo de retorno, y por qué la whitelist de
URLs *no* es parte de este contrato).

`Protocol` (no ABC), siguiendo el mismo criterio que `JobRepository`
(`app/domain/repositories/job_repository.py`) y el patrón ya fijado en
ADR-001 para `application/interfaces/`: contratos hacia sistemas externos
que un use case de `backend-engineer` orquesta. La implementación
concreta (`LinkedInFeedCollector`, Fase 2, `linkedin-agent`) vive en
`app/infrastructure/linkedin/linkedin_feed_collector.py` y satisface esta
interfaz por structural typing — Playwright nunca se filtra hacia
`application/` ni `domain/`.
"""

from __future__ import annotations

from typing import Protocol

from app.application.dto.raw_feed_post import RawFeedPost


class FeedCollector(Protocol):
    def collect(self) -> list[RawFeedPost]:
        """Returns the feed posts scraped in this run, as `RawFeedPost`.

        Contrato deliberadamente mínimo:

        - No recibe parámetros: el "dónde" (`linkedin.com/feed/`) y el
          "cuánto" (scroll controlado) son detalles de la implementación
          de `linkedin-agent`, no del contrato.
        - No garantiza ausencia de duplicados contra lo ya persistido en
          `jobs` — solo puede evitar duplicados *dentro* del propio DOM de
          esta corrida (LinkedIn puede renderizar el mismo post más de una
          vez mientras se hace scroll). La deduplicación contra
          `content_hash` ya guardado es responsabilidad de
          `CollectFeedPosts` (`app/application/use_cases/`), vía
          `JobRepository.get_by_content_hash`.
        - No conoce `JobRepository` ni la entidad `Job`: solo devuelve
          datos estructurados. Traducir `RawFeedPost` a `Job` (vía
          `Job.create(...)`) y persistir es responsabilidad exclusiva de
          `CollectFeedPosts`.
        - Puede propagar excepciones de infraestructura (p. ej. si
          Playwright intenta navegar fuera de la whitelist de
          `linkedin.com/feed/*`, o si la sesión expiró) — el tipo exacto
          de esas excepciones lo decide `linkedin-agent` dentro de su
          propio ownership (`app/infrastructure/linkedin/**`); este
          Protocol no las declara ni las conoce por nombre.
        """
        ...
