"""Port towards the CV catalog, consumed by `CVMatcher`
(`app/application/cv/matcher.py`).

`Protocol` (no ABC), mismo criterio que `FeedCollector`
(`app/application/interfaces/feed_collector.py`) y `LLMProvider`
(`app/application/interfaces/llm_provider.py`): un contrato hacia una fuente
externa (acá, un archivo YAML + filesystem) que `application/` orquesta sin
conocer el detalle concreto. La implementación
(`FilesystemCVRepository`, `app/infrastructure/cv/filesystem_cv_repository.py`)
la satisface por structural typing.

Se define en `app/application/cv/` -- no en `app/application/interfaces/`
junto a `FeedCollector`/`LLMProvider` -- porque ese directorio no está en el
ownership del CV Matching Agent (`docs/agents/AGENTS.md` sección 18: solo
`app/application/cv/**`, `app/infrastructure/cv/**`, `cvs/**`) y no hay
ninguna necesidad real hoy de que otro agente importe este Protocol desde
`interfaces/` -- YAGNI (`docs/agents/AGENTS.md` principio 9). Si en el
futuro otro caller fuera de `app/application/cv/` necesita depender de esta
abstracción, es una señal legítima para moverlo a `interfaces/` junto con
los demás Protocols, coordinando con `backend-engineer`.
"""

from __future__ import annotations

from typing import Protocol

from app.application.cv.cv_profile import CVProfile


class CVCatalog(Protocol):
    def list_cvs(self) -> list[CVProfile]:
        """Returns every CV in the catalog, in catalog (YAML) order.

        El orden de retorno es parte del contrato: `CVMatcher` lo usa como
        criterio de desempate cuando dos o más CVs matchean la misma
        cantidad de skills (gana el primero en aparecer aquí) -- ver
        docstring de `CVMatcher.match`.

        Puede propagar excepciones de infraestructura
        (`app.application.cv.cv_catalog_errors.CVInfrastructureError` --
        ver ADR-006, `docs/decisions/006-llm-and-cv-exception-boundaries.md`
        -- y sus subclases concretas, definidas en
        `app.infrastructure.cv.exceptions`) si el catálogo no existe, está
        mal formado, o un `file` referenciado no existe en disco -- este
        Protocol no las declara por nombre (Python no tiene `raises`
        tipado), mismo patrón que `FeedCollector.collect()`.
        """
        ...

    def get_summary(self, cv_id: str) -> str:
        """Returns the short, pre-written summary for a single `cv_id`.

        Pensado para que un futuro `GenerateApplicationEmail`
        (`app/application/use_cases/`, Fase 5) resuelva
        `EmailContext.cv_summary` sin tener que llamar `list_cvs()` y
        filtrar manualmente. Puede propagar
        `app.infrastructure.cv.exceptions.CVNotFoundError` si `cv_id` no
        existe en el catálogo.
        """
        ...
