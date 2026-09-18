"""`CVProfile`: plain data carrier for a single entry of the CV catalog
(`config/cvs.yaml`).

Ubicación deliberada en `app/application/cv/` (no en
`app/infrastructure/cv/`): `docs/agents/AGENTS.md` sección 20 lista
`CVProfile` explícitamente como uno de los "Shared Contracts" del proyecto,
al mismo nivel que `Job`/`JobAnalysis` -- un concepto que más de una capa
necesita nombrar (`app.application.cv.matcher.CVMatcher` lo consume para
matchear, y un futuro `GenerateApplicationEmail`
(`app/application/use_cases/`, ownership de `backend-engineer`/`llm-agent`)
lo va a necesitar para resolver `EmailContext.cv_summary`). Tratarlo como un
detalle de infraestructura forzaría a cualquier caller de application/ a
importar desde `app.infrastructure.cv`, violando la Regla de Dependencias
(`docs/agents/AGENTS.md` sección 26): infraestructura implementa hacia
adentro, application no depende hacia afuera. Mismo criterio ya aplicado a
`RawFeedPost`/`JobAnalysisResult`/`EmailContext` en `app/application/dto/`
-- este DTO vive en `app/application/cv/` en vez de `app/application/dto/`
porque `cv-matching-agent` no tiene ownership sobre `app/application/dto/`
(ver `docs/agents/AGENTS.md` sección 18: ese directorio no está en su
listado de ownership), y crear un archivo ahí sin necesidad real sería
tocar una carpeta compartida sin razón. `app/application/cv/` sí es
ownership explícito del CV Matching Agent.

Plain data carrier, dataclass congelado, sin invariantes de negocio (la
validación de que `file` exista en disco y `skills`/`summary` tengan la
forma correcta ocurre en `app.infrastructure.cv.filesystem_cv_repository`,
antes de construir esta instancia -- este DTO asume datos ya válidos).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CVProfile:
    """A single CV catalog entry, already validated by its repository.

    - `id`: la clave de la categoría en `config/cvs.yaml` (p. ej. `"java"`),
      usada como `recommended_cv` en `JobAnalysis.record_cv_recommendation`.
    - `file`: ruta declarada en el catálogo (relativa a la raíz del repo tal
      como se escribió en el YAML), no necesariamente absoluta -- quien
      necesite el path resuelto en disco para adjuntarlo (Fase 7,
      `gmail-agent`) es responsable de resolverlo contra el mismo `base_dir`
      que usó `FilesystemCVRepository` para validarlo.
    - `skills`: tal como están escritas en el catálogo (sin normalizar) --
      la normalización para matching es responsabilidad de
      `app.application.cv.skill_normalizer`, no de este DTO.
    - `summary`: resumen corto ya redactado (3-5 líneas), pensado para
      `EmailContext.cv_summary` (Fase 5) -- nunca el PDF completo, ver
      `TOKEN_OPTIMIZATION.md` §7.
    """

    id: str
    file: str
    skills: tuple[str, ...]
    summary: str
