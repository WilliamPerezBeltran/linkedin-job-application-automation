"""Root exception that is part of `CVCatalog`'s informal contract.

Ver `docs/decisions/006-llm-and-cv-exception-boundaries.md` (ADR-006) para
la decisión completa. Resumen: `GenerateApplicationEmail`
(`app/application/use_cases/generate_application_email.py`, ownership
`backend-engineer`) captura esta clase explícitamente
(`except CVInfrastructureError:`) al llamar
`CVCatalog.get_summary(...)` para no abortar el resto del batch ante un
`cv_id` que ya no existe en el catálogo -- por eso vive acá, en
`application/cv/`, en vez de en `app.infrastructure.cv.exceptions`
(violaría el dependency rule, `Infrastructure → Application interfaces →
Domain`, nunca al revés).

Vive en `app/application/cv/` -- junto al Protocol `CVCatalog`
(`app/application/cv/cv_catalog.py`) -- y no en
`app/application/interfaces/`, aunque quien la captura hoy sea un use case
de `backend-engineer`: el criterio (ADR-001 nota 3, ratificado por ADR-006)
es que una excepción que documenta el contrato informal de un Protocol
vive donde vive ese Protocol, y `CVCatalog` es ownership end-to-end de
`cv-matching-agent` (consumidor nativo `CVMatcher` + implementación
`FilesystemCVRepository`, ambos en su ownership). Que un agente externo
también consuma el mismo Protocol no cambia esa propiedad -- mismo
principio por el que `JobRepository` (ownership `domain-engineer`) no se
mueve a `application/` solo porque use cases de `backend-engineer`/
`llm-agent` lo consuman.

`app.infrastructure.cv.exceptions` (ownership `cv-matching-agent`) hereda
de esta clase -- no al revés. Las subclases concretas
(`CVCatalogNotFoundError`, `CVCatalogMalformedError`, `CVFileNotFoundError`,
`CVNotFoundError`) **no** se movieron acá porque `application/` no las
importa por nombre hoy (solo esta raíz) -- se quedan en
`app.infrastructure.cv.exceptions`, ahora heredando de esta clase en vez de
la raíz local que se retira.
"""

from __future__ import annotations


class CVInfrastructureError(Exception):
    """Root of every exception `CVCatalog` may propagate.

    Nunca se lanza directamente -- siempre una subclase concreta, definida
    en `app.infrastructure.cv.exceptions` (ownership `cv-matching-agent`),
    para que quien la capture (hoy, `GenerateApplicationEmail`) pueda
    distinguir "el catálogo no existe" de "un CV referenciado no existe en
    disco" de "el catálogo está mal formado" sin recurrir a
    `except Exception`.
    """
