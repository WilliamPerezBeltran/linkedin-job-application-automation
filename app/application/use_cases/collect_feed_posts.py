"""`CollectFeedPosts`: orchestrates a single LinkedIn feed scraping run.

Traduce cada `RawFeedPost` devuelto por `FeedCollector.collect()`
(`app/application/interfaces/feed_collector.py`) a la entidad `Job` (vía
`Job.create(...)`) y lo persiste a través de `JobRepository`, aplicando dos
reglas de resiliencia que ninguna de las dos dependencias garantiza por sí
sola (ver sus respectivos docstrings):

- Deduplicación contra lo ya persistido, por `content_hash`
  (`JobRepository.get_by_content_hash`) — `FeedCollector` solo evita
  duplicados *dentro* del propio DOM de una corrida.
- Un `RawFeedPost` que viola un invariante de dominio (p. ej. `url=""`,
  ver ADR-003 y `app/infrastructure/linkedin/parser.py`) se descarta con un
  warning en vez de abortar el resto del batch — mismo patrón de
  resiliencia que `linkedin_feed_collector.build_raw_feed_posts` ya aplica
  para posts sin contenido extraíble (`EmptyPostContentError`).

No abre ni cierra transacción: recibe un `JobRepository` ya construido
sobre una `Session` activa (ver `session_scope()` en
`app/infrastructure/database/session.py`) y solo llama a `save()` — el
commit/rollback final es responsabilidad de quien posee esa unidad de
trabajo (el composition root del endpoint, en este caso).

`collect()` (Playwright) y `persist()` (SQLAlchemy) están deliberadamente
separados en dos métodos públicos, en vez de un único `execute()` opaco: el
scraping real (`FeedCollector.collect()`) no debe ocurrir dentro de la
transacción abierta por `session_scope()` (ver el límite transaccional
documentado en `app/infrastructure/database/session.py` y
`sqlalchemy_job_repository.py` — nunca mantener una transacción abierta
durante una llamada a Playwright/LLM/Gmail). El composition root del
endpoint (`app/presentation/api/routes/scrape.py`) llama primero a
`collect_posts()` fuera de cualquier `session_scope()`, y recién después
abre la transacción para llamar a `persist(raw_posts)`. `execute()` sigue
existiendo como atajo conveniente para scripts/tests que no necesitan
acotar la transacción por separado (p. ej. los unit tests de este módulo).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from app.application.dto.raw_feed_post import RawFeedPost
from app.application.interfaces.feed_collector import FeedCollector
from app.domain.entities.job import Job
from app.domain.exceptions.invalid_domain_value_error import InvalidDomainValueError
from app.domain.repositories.job_repository import JobRepository

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CollectFeedPostsResult:
    """Counters summarizing a single `CollectFeedPosts` persistence run."""

    saved: int
    skipped_duplicates: int
    skipped_invalid: int


class CollectFeedPosts:
    """Use case: scrape the LinkedIn feed once and persist new `Job`s."""

    def __init__(self, feed_collector: FeedCollector, job_repository: JobRepository) -> None:
        self._feed_collector = feed_collector
        self._job_repository = job_repository

    def execute(self) -> CollectFeedPostsResult:
        """Convenience: collects and persists in a single call.

        Solo apto cuando a quien llama no le importa acotar por separado la
        transacción de persistencia (p. ej. estos unit tests, con un
        `JobRepository` fake que no abre ninguna transacción real). El
        composition root de `POST /scrape` no usa este método — ver
        `collect_posts()`/`persist()` y el docstring del módulo.
        """
        raw_posts = self.collect_posts()
        return self.persist(raw_posts)

    def collect_posts(self) -> list[RawFeedPost]:
        """Delegates to `FeedCollector.collect()`. No toca `JobRepository`.

        Pensado para correr **fuera** de cualquier `session_scope()` — es la
        parte de este use case respaldada por Playwright, no por SQLAlchemy.
        """
        return self._feed_collector.collect()

    def persist(self, raw_posts: list[RawFeedPost]) -> CollectFeedPostsResult:
        """Translates and persists already-collected `raw_posts`.

        Pensado para correr **dentro** de un `session_scope()` — es la
        única parte de este use case que toca `JobRepository`.
        """
        saved = 0
        skipped_duplicates = 0
        skipped_invalid = 0
        now = datetime.now(UTC)

        for raw_post in raw_posts:
            if self._job_repository.get_by_content_hash(raw_post.content_hash) is not None:
                skipped_duplicates += 1
                continue

            try:
                job = Job.create(
                    source="linkedin",
                    author=raw_post.author,
                    content=raw_post.content,
                    content_hash=raw_post.content_hash,
                    email=None,
                    url=raw_post.url,
                    published_at=raw_post.published_at,
                    scraped_at=now,
                    created_at=now,
                )
            except InvalidDomainValueError as exc:
                logger.warning(
                    "collect_feed_posts.skip_invalid_post content_hash=%s reason=%s",
                    raw_post.content_hash,
                    exc,
                )
                skipped_invalid += 1
                continue

            self._job_repository.save(job)
            saved += 1

        return CollectFeedPostsResult(
            saved=saved,
            skipped_duplicates=skipped_duplicates,
            skipped_invalid=skipped_invalid,
        )
