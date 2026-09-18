"""Normaliza el `innerHTML` crudo de un post del feed (extraído por
`feed.collect_raw_posts_html`) a un dato estructurado y limpio
(`ParsedFeedPost`).

Tolerante a cambios menores del DOM: LinkedIn cambia nombres de clase y
estructura con frecuencia, así que cada campo prueba una lista de
selectores alternativos, y los campos no esenciales (`author`, `url`,
`published_at`) degradan a un valor por defecto sensato en vez de fallar.
El único campo esencial es `content` — sin texto no hay nada que el Job
Analyzer (Fase 3) pueda evaluar, así que su ausencia sí levanta
`EmptyPostContentError` de forma explícita.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from bs4 import BeautifulSoup

from app.infrastructure.linkedin.exceptions import EmptyPostContentError

# Selectores alternativos, probados en orden — mezcla de clases conocidas de
# `feed-shared-update-v2`/`update-components-*` (estructura real de LinkedIn
# al momento de escribir esto) y de `data-testid` como fallback más estable
# ante refactors puramente cosméticos del DOM.
_CONTENT_SELECTORS = (
    ".update-components-text",
    "[data-testid='post-content']",
    ".feed-shared-update-v2__description",
    ".feed-shared-text",
)
_AUTHOR_SELECTORS = (
    ".update-components-actor__name",
    "[data-testid='post-author-name']",
    ".feed-shared-actor__name",
)
_TIMESTAMP_SELECTORS = (
    ".update-components-actor__sub-description",
    "[data-testid='post-timestamp']",
    ".feed-shared-actor__sub-description",
)
_URL_SELECTORS = (
    "a.app-aware-link[href*='/feed/update/']",
    "a[href*='/feed/update/']",
)

_FALLBACK_AUTHOR = "Unknown LinkedIn member"
_LINKEDIN_ORIGIN = "https://www.linkedin.com"

# LinkedIn muestra timestamps relativos ("2h", "3d", "1 sem", "5min").
# Reconoce el patrón común "<numero><unidad>"; cualquier formato no
# reconocido degrada a `published_at=None` en vez de fallar.
_RELATIVE_TIME_RE = re.compile(r"(\d+)\s*(mo|sem|min|hr|[smhdw])\b", re.IGNORECASE)
_UNIT_TO_TIMEDELTA: dict[str, timedelta] = {
    "s": timedelta(seconds=1),
    "min": timedelta(minutes=1),
    "m": timedelta(minutes=1),
    "hr": timedelta(hours=1),
    "h": timedelta(hours=1),
    "d": timedelta(days=1),
    "w": timedelta(weeks=1),
    "sem": timedelta(weeks=1),
    "mo": timedelta(days=30),  # aproximado — LinkedIn no da precisión mayor
}


@dataclass(frozen=True, slots=True)
class ParsedFeedPost:
    """Resultado de parsear un único post — insumo directo para que
    `linkedin_feed_collector.build_raw_feed_posts` calcule `content_hash`
    y arme el `RawFeedPost` final.
    """

    author: str
    content: str
    url: str
    published_at: datetime | None


def parse_post(post_html: str, *, now: datetime | None = None) -> ParsedFeedPost:
    """Parses one post's raw HTML into a `ParsedFeedPost`.

    `now` es inyectable para que los tests sean determinísticos al
    resolver timestamps relativos ("2h" -> `now - timedelta(hours=2)`);
    en producción se usa `datetime.now(UTC)`.
    """
    soup = BeautifulSoup(post_html, "lxml")

    content = _normalize_whitespace(_first_text(soup, _CONTENT_SELECTORS) or "")
    if not content:
        raise EmptyPostContentError(
            "Feed post has no extractable text content — refusing to return "
            "an empty/unusable post. This usually means LinkedIn's DOM "
            "structure changed (selectors in parser.py need updating), not "
            "that the post itself is genuinely empty."
        )

    author = _normalize_whitespace(_first_text(soup, _AUTHOR_SELECTORS) or "") or _FALLBACK_AUTHOR
    url = _resolve_url(_first_href(soup, _URL_SELECTORS))
    published_at = _extract_published_at(soup, now=now or datetime.now())

    return ParsedFeedPost(author=author, content=content, url=url, published_at=published_at)


def _first_text(soup: BeautifulSoup, selectors: tuple[str, ...]) -> str | None:
    for selector in selectors:
        element = soup.select_one(selector)
        if element is not None:
            text = element.get_text(separator=" ", strip=True)
            if text:
                return text
    return None


def _first_href(soup: BeautifulSoup, selectors: tuple[str, ...]) -> str | None:
    for selector in selectors:
        element = soup.select_one(selector)
        if element is not None:
            href = element.get("href")
            if isinstance(href, str) and href:
                return href
    return None


def _resolve_url(href: str | None) -> str:
    if not href:
        return ""
    if href.startswith("http://") or href.startswith("https://"):
        return href
    if href.startswith("/"):
        return f"{_LINKEDIN_ORIGIN}{href}"
    return href


def _extract_published_at(soup: BeautifulSoup, *, now: datetime) -> datetime | None:
    for selector in _TIMESTAMP_SELECTORS:
        element = soup.select_one(selector)
        if element is None:
            continue
        text = element.get_text(separator=" ", strip=True)
        match = _RELATIVE_TIME_RE.search(text)
        if not match:
            continue
        amount = int(match.group(1))
        unit = match.group(2).lower()
        unit_delta = _UNIT_TO_TIMEDELTA.get(unit)
        if unit_delta is None:
            continue
        return now - (unit_delta * amount)
    return None


def _normalize_whitespace(text: str) -> str:
    """Collapses runs of whitespace (including newlines from `<br>`-split
    text nodes) into single spaces and trims the result — this is the
    "contenido normalizado" sobre el que `linkedin_feed_collector.py`
    calcula `content_hash` (mismo texto, sin variación espuria de
    espacios entre dos scrapes del mismo post).
    """
    return re.sub(r"\s+", " ", text).strip()
