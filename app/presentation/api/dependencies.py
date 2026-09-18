"""FastAPI dependency providers consumed by `app/presentation/api/routes/jobs.py`.

Centraliza la construcción de repositorios/proveedores concretos
(`SQLAlchemyJobRepository`, `SQLAlchemyJobAnalysisRepository`,
`AnthropicProvider`, `FilesystemCVRepository`, `CVMatcher`) detrás de
funciones `Depends`-compatibles de FastAPI, en vez de repetir esa
construcción inline en cada uno de los cinco endpoints nuevos de Fase 6.

Decisión de diseño (`backend-engineer`, Fase 6): `POST /scrape`
(`app/presentation/api/routes/scrape.py`, Fase 2) construye sus dependencias
a mano dentro del propio handler -- razonable para un único endpoint, pero
con cinco endpoints nuevos que comparten las mismas piezas de
infraestructura esa construcción inline se volvería una duplicación real.
Además, `code-reviewer` ya había dejado una observación LOW sobre
`scrape.py` señalando que construir dependencias a mano "es más frágil a
refactors que `app.dependency_overrides`" -- introducir este módulo resuelve
esa observación para el código nuevo de esta fase (no se retrofittea
`scrape.py`, que queda fuera del alcance de esta tarea; su endpoint único no
justifica el mismo refactor por sí solo).

`get_db_session` envuelve `session_scope()`
(`app/infrastructure/database/session.py`) como una dependencia FastAPI con
`yield`: FastAPI abre la `Session` antes de correr el handler y, al volver
(éxito o excepción propagada desde el handler), reanuda el generador --
`session_scope()` hace `commit()` si no hubo excepción o `rollback()` si la
hubo, exactamente el mismo contrato transaccional que ya usa `scrape.py` con
`with session_scope() as session:`, solo que orquestado por FastAPI en vez
de manualmente (ver `docs/agents/AGENTS.md` sección 13 sobre límites
transaccionales).

FastAPI cachea el resultado de cada dependencia por request (comportamiento
por default, no se pasa `use_cache=False` en ningún lado de este módulo),
así que `get_job_repository` y `get_job_analysis_repository` -- ambos
dependientes de `get_db_session` -- reciben la **misma** `Session` dentro de
un mismo request. Eso preserva la atomicidad de escritura conjunta que exige
ADR-004 sección 2 (`docs/decisions/004-job-pipeline-repositories.md`): un
único commit al final del request, cubriendo los `save()` de `Job` y
`JobAnalysis` que un mismo handler haga (p. ej. el encadenamiento
`AnalyzeJobPost` -> `SelectBestCV` dentro de `POST /api/jobs/{id}/analyze`).

Los proveedores sin estado por-request (`AnthropicProvider`,
`FilesystemCVRepository`, `CVMatcher`) no dependen de `get_db_session`: se
construyen una vez por request. No hay necesidad real hoy de compartirlos
entre requests (p. ej. cacheando el cliente HTTP de Anthropic a nivel de
proceso) -- YAGNI, se puede optimizar si el costo de construcción resulta
medible.

Fase 7 (segunda ronda, `POST /api/jobs/{id}/create-draft`) agrega dos
proveedores más, siguiendo los mismos dos criterios ya fijados arriba:

- `get_application_repository` depende de `get_db_session`, igual que
  `get_job_repository`/`get_job_analysis_repository` -- comparte la misma
  `Session` de request para que `CreateGmailDraft.execute_one` (que guarda
  tanto la `Application` nueva como el `Job` recién transicionado a
  `DRAFT_CREATED`) confirme ambos cambios en un único commit.
- `get_email_draft_repository` es sin estado por-request, mismo criterio que
  `get_llm_provider`/`get_cv_catalog`: `GmailDraftRepository()` sin
  argumentos usa sus defaults de producción (incluyendo el flujo OAuth real
  de `google-auth-oauthlib` la primera vez que se invoque `create_draft`,
  ver su docstring) -- no hay necesidad real hoy de cachear ese cliente a
  nivel de proceso (YAGNI, igual que `AnthropicProvider`).
"""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Depends
from sqlalchemy.orm import Session

from app.application.cv.cv_catalog import CVCatalog
from app.application.cv.matcher import CVMatcher
from app.application.interfaces.email_draft_repository import EmailDraftRepository
from app.application.interfaces.llm_provider import LLMProvider
from app.domain.repositories.application_repository import ApplicationRepository
from app.domain.repositories.job_analysis_repository import JobAnalysisRepository
from app.domain.repositories.job_repository import JobRepository
from app.infrastructure.cv.filesystem_cv_repository import FilesystemCVRepository
from app.infrastructure.database.repositories.sqlalchemy_application_repository import (
    SQLAlchemyApplicationRepository,
)
from app.infrastructure.database.repositories.sqlalchemy_job_analysis_repository import (
    SQLAlchemyJobAnalysisRepository,
)
from app.infrastructure.database.repositories.sqlalchemy_job_repository import (
    SQLAlchemyJobRepository,
)
from app.infrastructure.database.session import session_scope
from app.infrastructure.gmail.gmail_draft_repository import GmailDraftRepository
from app.infrastructure.llm.anthropic_provider import AnthropicProvider


def get_db_session() -> Iterator[Session]:
    """Yields a `Session` scoped to a single request (one commit/rollback
    by default).

    Ver el docstring del módulo -- envuelve `session_scope()` para que
    FastAPI administre el ciclo de vida de la transacción alrededor del
    handler que la solicite (directa o transitivamente vía
    `get_job_repository`/`get_job_analysis_repository`).

    Un handler puede pedir esta dependencia directamente (además de vía los
    repositorios) para hacer un `session.commit()` explícito e intermedio
    cuando encadena más de una unidad de trabajo lógicamente independiente
    en el mismo request -- único caso hoy: `POST /api/jobs/{id}/analyze`
    (`app/presentation/api/routes/jobs.py`), que confirma el resultado de
    `AnalyzeJobPost.execute_one` antes de intentar el `SelectBestCV`
    encadenado, para que un fallo en el segundo paso no revierta el primero
    (ver el docstring de ese endpoint para el detalle completo). El
    `session_scope()` que envuelve esta dependencia sigue haciendo su propio
    commit/rollback final al cerrar el request -- un `commit()` de más sobre
    una `Session` sin cambios pendientes es una operación segura y barata en
    SQLAlchemy.
    """
    with session_scope() as session:
        yield session


def get_job_repository(session: Session = Depends(get_db_session)) -> JobRepository:
    """Returns a `JobRepository` bound to the request-scoped `Session`."""
    return SQLAlchemyJobRepository(session)


def get_job_analysis_repository(
    session: Session = Depends(get_db_session),
) -> JobAnalysisRepository:
    """Returns a `JobAnalysisRepository` bound to the request-scoped `Session`.

    Comparte la misma `Session` que `get_job_repository` dentro de un mismo
    request (caching de dependencias de FastAPI) -- ver docstring del
    módulo.
    """
    return SQLAlchemyJobAnalysisRepository(session)


def get_llm_provider() -> LLMProvider:
    """Returns the concrete `LLMProvider` (Anthropic) for this request."""
    return AnthropicProvider()


def get_cv_catalog() -> CVCatalog:
    """Returns the concrete `CVCatalog` (filesystem-backed) for this request."""
    return FilesystemCVRepository()


def get_cv_matcher(cv_catalog: CVCatalog = Depends(get_cv_catalog)) -> CVMatcher:
    """Returns a `CVMatcher` built over the request-scoped `CVCatalog`."""
    return CVMatcher(cv_catalog)


def get_application_repository(
    session: Session = Depends(get_db_session),
) -> ApplicationRepository:
    """Returns an `ApplicationRepository` bound to the request-scoped `Session`.

    Comparte la misma `Session` que `get_job_repository` dentro de un mismo
    request (caching de dependencias de FastAPI) -- ver docstring del
    módulo, sección Fase 7.
    """
    return SQLAlchemyApplicationRepository(session)


def get_email_draft_repository() -> EmailDraftRepository:
    """Returns the concrete `EmailDraftRepository` (real Gmail API) for this request.

    Sin argumentos: usa los defaults de producción de `GmailDraftRepository`
    (`credentials_path`/`token_path`/`base_dir` resueltos internamente).
    `GmailDraftRepository.__init__` llama a
    `gmail_client.build_gmail_service` de forma síncrona en cada
    construcción -- no en `create_draft` -- así que el flujo OAuth
    interactivo real (o, con un token ya cacheado en disco, solo su
    refresh) puede dispararse en cualquier request a este endpoint, no
    únicamente en el "primer" request: esta dependencia se reconstruye una
    vez por request (sin caching a nivel de proceso, ver docstring del
    módulo), igual que `get_llm_provider`/`get_cv_catalog`. Con un token ya
    cacheado, ese refresh es barato; ver el docstring de
    `GmailDraftRepository.__init__` para el detalle completo.
    """
    return GmailDraftRepository()
