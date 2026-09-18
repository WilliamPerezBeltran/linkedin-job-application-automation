"""Infrastructure-level exceptions for `app.infrastructure.cv`.

Deliberadamente **no** heredan de
`app.domain.exceptions.domain_error.DomainError`: esa jerarquía está
reservada a violaciones de invariantes de *negocio* (`Job`, `JobAnalysis`,
`EmailAddress`, etc.). Todo lo que ocurre en este módulo es infraestructura
no confiable -- un archivo YAML de configuración y el filesystem local --
mismo criterio ya usado por `app.infrastructure.llm.exceptions` y
`app.infrastructure.linkedin.exceptions`: raíz abstracta + subclases
concretas, nunca se lanza la raíz directamente, y nunca
`except Exception: pass` (ver `docs/agents/AGENTS.md` sección 23 y
principio 15). (Nota post-ADR-006: `app.infrastructure.llm.exceptions` ya
no define una raíz local propia -- ver su propio docstring -- pero el
criterio de "raíz + subclases concretas" sigue vigente acá porque las 4
subclases de este módulo sí necesitan distinguirse entre sí.)

**Jerarquía tras ADR-006** (`docs/decisions/006-llm-and-cv-exception-boundaries.md`):
la raíz `CVInfrastructureError` de este módulo ya no hereda directo de
`Exception` -- hereda de
`app.application.cv.cv_catalog_errors.CVInfrastructureError`, la clase
homónima que vive junto al Protocol `CVCatalog`
(`app/application/cv/cv_catalog.py`), porque es la única de esta jerarquía
que `application/` importa y captura por nombre hoy
(`GenerateApplicationEmail`, `app/presentation/api/routes/jobs.py`). Esto
resuelve el hallazgo `MEDIUM` de `code-reviewer` (import
`application/` → `infrastructure/`) sin mover código que no cruza el
boundary: las cuatro subclases concretas siguen viviendo acá, sin cambios
de nombre, mensaje ni comportamiento, porque ningún módulo de
`application/` las importa por nombre hoy -- solo la raíz.
"""

from __future__ import annotations

from app.application.cv.cv_catalog_errors import (
    CVInfrastructureError as _CVInfrastructureErrorBase,
)


class CVInfrastructureError(_CVInfrastructureErrorBase):
    """Root of every exception raised by `app.infrastructure.cv`.

    Nunca se lanza directamente -- siempre una subclase concreta, para que
    quien la capture (hoy, `GenerateApplicationEmail`) pueda distinguir "el
    catálogo no existe" de "un CV referenciado no existe en disco" de "el
    catálogo está mal formado" sin recurrir a `except Exception`.

    Hereda de `app.application.cv.cv_catalog_errors.CVInfrastructureError`
    (ADR-006) en vez de `Exception` directamente -- ver docstring del
    módulo.
    """


class CVCatalogNotFoundError(CVInfrastructureError):
    """`config/cvs.yaml` (o la ruta de catálogo configurada) no existe en disco."""

    def __init__(self, catalog_path: str) -> None:
        super().__init__(f"CV catalog file not found: {catalog_path}")
        self.catalog_path = catalog_path


class CVCatalogMalformedError(CVInfrastructureError):
    """`config/cvs.yaml` existe pero su contenido no tiene la forma esperada
    (no es un mapeo, falta la clave `cvs`, o una entrada no tiene
    `file`/`skills`/`summary` con el tipo correcto)."""

    def __init__(self, catalog_path: str, reason: str) -> None:
        super().__init__(f"Malformed CV catalog at {catalog_path}: {reason}")
        self.catalog_path = catalog_path
        self.reason = reason


class CVFileNotFoundError(CVInfrastructureError):
    """El `file` declarado para un CV en el catálogo no existe en disco.

    Deliberadamente estricto (falla en vez de degradar en silencio): un CV
    recomendado cuyo archivo no existe rompería la Fase 7 (adjunto en el
    draft de Gmail) de forma silenciosa y tardía si no se detecta acá.
    """

    def __init__(self, cv_id: str, resolved_path: str) -> None:
        super().__init__(f"CV file for '{cv_id}' not found at resolved path: {resolved_path}")
        self.cv_id = cv_id
        self.resolved_path = resolved_path


class CVNotFoundError(CVInfrastructureError):
    """Se pidió un `cv_id` (p. ej. vía `get_summary`) que no existe en el catálogo."""

    def __init__(self, cv_id: str) -> None:
        super().__init__(f"CV id not found in catalog: {cv_id}")
        self.cv_id = cv_id
