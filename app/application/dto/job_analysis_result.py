"""`JobAnalysisResult`: the DTO returned by `LLMProvider.analyze_job()`.

Ver `docs/decisions/005-llm-provider-interface.md` (ADR-005) sección 2 para
la decisión completa. Resumen: `llm-agent` (`app/infrastructure/llm/**`)
nunca construye la entidad de dominio `JobAnalysis` ni conoce sus
invariantes (`match_score` en `[0,1]`, "no `generated_email` sin
`recommended_cv`") — `LLMProvider.analyze_job()` devuelve este DTO plano, y
es el use case `AnalyzeJobPost` (`backend-engineer`,
`app/application/use_cases/analyze_job_post.py`, todavía no creado) quien lo
traduce a `JobAnalysis` vía su constructor, y a `Job.mark_analyzed()` /
`Job.mark_relevant()` / `Job.mark_not_relevant()`.

Mismo criterio que `RawFeedPost` (ADR-003): plain data carrier, dataclass
congelado, sin `__post_init__` con invariantes de negocio. En particular, la
lista cerrada de categorías (Java, Python, AI/ML, Deep Learning,
JavaScript/Node, Go, Elixir, Full Stack, Other) **no** se valida aquí — esa
validación vive en el modelo Pydantic interno del proveedor concreto
(ownership `llm-agent`); este DTO es deliberadamente tan laxo como la propia
entidad `JobAnalysis` (que solo exige `job_type` no vacío).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class JobAnalysisResult:
    """Structured output of the Job Analyzer LLM for a single job post.

    - `is_job`: si el post analizado es efectivamente una oferta de empleo
      relevante (dev, IA, ML, deep learning). Si es `False`, `AnalyzeJobPost`
      no construye `JobAnalysis` — solo marca el `Job` como
      `NOT_RELEVANT`, el resto de los campos se ignora.
    - `job_type`: categoría detectada, en el vocabulario del dominio (mismo
      nombre de campo que `JobAnalysis.job_type`, no `job_category` como en
      el JSON crudo del proveedor — la traducción de esa clave ocurre dentro
      del proveedor concreto, ver ADR-005 sección 2).
    - `email_addresses`: direcciones de contacto extraídas del texto del
      post, si las hay. Nota: al momento de este ADR, `Job` no tiene forma
      de persistir un email extraído post-creación (deuda técnica
      `DEBT-ADR005-01`, ver ADR-005 "Risks") — este campo queda definido
      igual, reflejando lo que el proveedor puede extraer, independientemente
      de dónde se persista.
    - `confidence`: campo transitorio, sin columna equivalente en
      `JobAnalysis`/`job_analysis`. Solo lo usa `AnalyzeJobPost` para decidir
      ruteo/umbral de confianza antes de marcar `RELEVANT`; no se persiste
      hoy (ver ADR-005 sección 2).
    """

    is_job: bool
    job_type: str
    seniority: str | None
    skills: tuple[str, ...]
    languages: tuple[str, ...]
    frameworks: tuple[str, ...]
    cloud: tuple[str, ...]
    ai_related: bool
    email_addresses: tuple[str, ...]
    confidence: float
