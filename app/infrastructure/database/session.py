"""SQLAlchemy engine and `Session` factory (synchronous — see ADR-002).

Lee `DATABASE_URL` desde el entorno (`.env` cargado vía `python-dotenv`).

Nota de ownership (ver ROADMAP.md Fase 1 y ADR-001, sección "Ownership de
`infrastructure/config/**`"): el `Settings` tipado con `pydantic-settings`
que centraliza toda la configuración de la app es ownership de
`backend-engineer` y todavía no existe (`app/infrastructure/config/` está
vacío salvo `__init__.py`). Mientras tanto, este módulo hace una lectura
mínima y local de `DATABASE_URL` vía `os.environ`/`python-dotenv`, sin
crear ese `Settings` compartido — cuando `backend-engineer` lo implemente,
este módulo puede migrar a consumirlo en vez de leer el entorno
directamente.

Límites transaccionales (ver `docs/agents/AGENTS.md` sección 13): las
transacciones abiertas con `session_scope()` deben limitarse a operaciones
de base de datos. Nunca mantener una transacción abierta durante llamadas
a LLM, scraping con Playwright o Gmail — esas llamadas externas ocurren
fuera de este context manager, con su resultado ya calculado, y solo se
persiste dentro de la transacción.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from dotenv import load_dotenv
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

# Carga `.env` si existe (no sobreescribe variables ya presentes en el
# entorno real, p. ej. las inyectadas por CI o docker-compose).
load_dotenv()

_DEFAULT_DATABASE_URL = "postgresql://user:password@localhost:5432/jobs_db"


def get_database_url() -> str:
    """Returns the configured `DATABASE_URL`, falling back to the local dev default.

    El fallback coincide con `.env.example` / `docker-compose.yml` para que
    `alembic upgrade head` y los tests de integración funcionen out-of-the-box
    contra un `docker-compose up -d postgres` local sin configuración extra.
    """
    return os.environ.get("DATABASE_URL", _DEFAULT_DATABASE_URL)


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Returns the process-wide SQLAlchemy `Engine` (created lazily, once).

    Un solo `Engine` por proceso es el patrón estándar de SQLAlchemy: el
    `Engine` administra un pool de conexiones internamente y es seguro de
    compartir entre threads (a diferencia de una `Session`, ver
    `session_scope()` más abajo).

    `hide_parameters=True`: por default SQLAlchemy incluye los parámetros
    bind (p. ej. el `content`/`author`/`url` completos de un `Job`) en la
    representación en texto de una excepción como `IntegrityError` — eso
    puede terminar logueado tal cual por código que hace `logger.error(...,
    exc)` (ver `app/presentation/api/routes/scrape.py`), filtrando contenido
    potencialmente largo o sensible del post scrapeado a los logs. Desactivar
    esto es la mitigación correcta a nivel de `Engine`, sin depender de que
    cada caller recuerde no loguear la excepción cruda.
    """
    return create_engine(get_database_url(), pool_pre_ping=True, future=True, hide_parameters=True)


@lru_cache(maxsize=1)
def get_session_factory() -> sessionmaker[Session]:
    """Returns the process-wide `sessionmaker`, bound to `get_engine()`."""
    return sessionmaker(bind=get_engine(), autoflush=False, expire_on_commit=False, future=True)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Yields a `Session` scoped to a single unit of work (commit/rollback/close).

    Patrón "una `Session` por unidad de trabajo" (ADR-002): una `Session`
    síncrona no es thread-safe y no debe compartirse entre threads ni
    reutilizarse entre requests/llamadas. Cada llamador (repositorio, script
    de test) abre su propia sesión con este context manager y la cierra al
    salir del bloque `with`, haciendo commit si no hubo excepción o rollback
    si la hubo.
    """
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
