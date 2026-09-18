"""`LinkedInFeedCollector`: implementación concreta de `FeedCollector`
(`app/application/interfaces/feed_collector.py`, ver ADR-003) respaldada
por Playwright.

Satisface el `Protocol` por *structural typing* — no hereda de nada, no
importa `JobRepository` ni la entidad `Job`. Su única responsabilidad es
devolver `list[RawFeedPost]`; nunca persiste nada (`CollectFeedPosts`,
ownership de `backend-engineer`, es quien traduce a `Job` y hace el
upsert/dedup contra la base de datos).

`collect()` **nunca** intenta loguearse: si `storage_state.json` no existe
o la sesión persistida expiró, falla con `LinkedInAuthenticationError`
indicando que hay que correr `session.run_manual_login` por separado (ver
docstring de `session.py`).
"""

from __future__ import annotations

import hashlib
import logging

from app.application.dto.raw_feed_post import RawFeedPost
from app.infrastructure.linkedin.browser import (
    launch_chromium,
    open_context,
    persist_storage_state,
)
from app.infrastructure.linkedin.config import LinkedInSessionConfig, load_config_from_env
from app.infrastructure.linkedin.exceptions import (
    EmptyPostContentError,
    LinkedInAuthenticationError,
)
from app.infrastructure.linkedin.feed import collect_raw_posts_html
from app.infrastructure.linkedin.parser import parse_post
from app.infrastructure.linkedin.session import verify_active_session

logger = logging.getLogger(__name__)


class LinkedInFeedCollector:
    """Playwright-backed `FeedCollector`. Ver `docs/decisions/003-feed-collector-interface.md`."""

    def __init__(self, config: LinkedInSessionConfig | None = None) -> None:
        self._config = config or load_config_from_env()

    def collect(self) -> list[RawFeedPost]:
        """Scrapes the LinkedIn feed once and returns `RawFeedPost` per post.

        No recibe parámetros ni garantiza dedup contra lo ya persistido
        (eso es de `CollectFeedPosts`) — ver el Protocol para el contrato
        completo.
        """
        if not self._config.storage_state_path.is_file():
            raise LinkedInAuthenticationError(
                f"No session found at '{self._config.storage_state_path}'. "
                "Run `app.infrastructure.linkedin.session.run_manual_login` "
                "once, in a visible browser, before collecting."
            )

        raw_html_posts = self._scrape_raw_html_posts()
        return build_raw_feed_posts(raw_html_posts)

    def _scrape_raw_html_posts(self) -> list[str]:
        with launch_chromium(headless=self._config.headless) as browser:
            context = open_context(browser, storage_state_path=self._config.storage_state_path)
            try:
                page = context.new_page()
                try:
                    verify_active_session(page, feed_url=self._config.feed_url)
                    return collect_raw_posts_html(page, config=self._config)
                finally:
                    page.close()
            finally:
                persist_storage_state(context, storage_state_path=self._config.storage_state_path)
                context.close()


def build_raw_feed_posts(raw_html_posts: list[str]) -> list[RawFeedPost]:
    """Parses each raw post HTML snippet into a `RawFeedPost`, computing
    `content_hash = sha256(normalized_content)`.

    Función pura (sin Playwright) a propósito separada de `collect()`: es
    lo que permite testear el mapeo `HTML -> RawFeedPost` (incluido el
    cálculo del hash) contra fixtures, sin un navegador real.

    Resiliencia del scraping: si un post individual no tiene contenido
    extraíble (`EmptyPostContentError`, ver `parser.py`), se descarta con
    un warning en vez de abortar toda la corrida por un solo post
    malformado — el resto de excepciones de `parser.parse_post` (p. ej. un
    error real de `BeautifulSoup`) sí se propagan, porque no son un caso
    esperado de "contenido incompleto".
    """
    posts: list[RawFeedPost] = []
    for raw_html in raw_html_posts:
        try:
            parsed = parse_post(raw_html)
        except EmptyPostContentError:
            logger.warning("linkedin.feed.skip_empty_post")
            continue

        content_hash = hashlib.sha256(parsed.content.encode("utf-8")).hexdigest()
        posts.append(
            RawFeedPost(
                author=parsed.author,
                content=parsed.content,
                content_hash=content_hash,
                url=parsed.url,
                published_at=parsed.published_at,
            )
        )
    return posts
