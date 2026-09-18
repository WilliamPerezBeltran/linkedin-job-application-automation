"""Cheap, LLM-free pre-filter deciding whether a scraped post is worth
sending to `LLMProvider.analyze_job()` at all.

Ver `TOKEN_OPTIMIZATION.md` §2 ("Filtrar antes de llamar al LLM"): el
Analyzer corre sobre *todo* lo scrapeado, incluyendo ruido no relacionado
con empleo — este filtro por keywords, gratis y determinístico, puede
recortar ese volumen entre 60-90% antes de gastar un solo token.

**Reubicado desde `app.infrastructure.llm.candidate_filter` (decisión del
orchestrator, post code-review de Fase 3).** `llm-agent` lo había colocado
originalmente en `app/infrastructure/llm/` con el razonamiento de "vive
junto al proveedor que protege". `code-reviewer` marcó como CONFIRMED de
severidad alta que `AnalyzeJobPost`
(`app/application/use_cases/analyze_job_post.py`) importándolo desde ahí
viola el dependency rule (`ENGINEERING_STANDARDS.md`/`docs/agents/AGENTS.md`
§26: Application no debe depender de Infrastructure) — a diferencia de
`LLMProviderError`/`LLMResponseValidationError`, que en su momento seguían
importándose desde `app.infrastructure.llm.exceptions` sin que ADR-005 lo
resolviera explícitamente (dejó el punto abierto, ver ADR-006 sección
"Problem"), esta función no envuelve ningún tipo de excepción de un SDK
externo: es lógica pura sin ninguna dependencia de infraestructura (no
importa Anthropic/OpenAI, no hace I/O, no toca SQLAlchemy). No hay ninguna
razón arquitectónica para que viva fuera de `application/` — la única razón
original era "está cerca del proveedor que protege", que es un criterio de
conveniencia, no de dependencia real. `LLMProviderError`/
`LLMResponseValidationError` ya se resolvieron aparte: ADR-006
(`docs/decisions/006-llm-and-cv-exception-boundaries.md`) las movió a
`app/application/interfaces/llm_provider_errors.py`, cerrando ese punto sin
tocar esta función. El precedente de Fase 2 sobre
`app/presentation/api/routes/scrape.py` importando excepciones de
`app.infrastructure.linkedin.exceptions` sigue siendo un caso análogo
pendiente, documentado como deuda técnica separada en ADR-006
(`DEBT-ADR006-02`), fuera de este módulo.

Esto es exactamente el escenario que `docs/architecture/clean-architecture-
skeleton.md` (nota de diseño 2) ya anticipó: `application/services/` se
crea "cuando aparece lógica compartida entre múltiples use cases que no
encaja como entidad/servicio de dominio" — un filtro de costo de LLM,
reutilizable por cualquier use case futuro que quiera evitar invocar
`LLMProvider` innecesariamente, encaja exactamente ahí.

Mismo contenido y comportamiento que el módulo original (ver historial de
`llm-agent`, Fase 3) — solo cambia la ubicación y este docstring.
"""

from __future__ import annotations

_MIN_CONTENT_LENGTH = 40

# Deliberadamente amplia y conservadora hacia falsos positivos: es más barato
# mandar algo de ruido de más al LLM que descartar por error una oferta real
# antes de que el Analyzer la vea. Minúsculas — la comparación normaliza
# `content` con `.lower()` antes de buscar.
_JOB_KEYWORDS: tuple[str, ...] = (
    # Genéricas de empleo (inglés/español)
    "hiring",
    "we're hiring",
    "job opening",
    "vacante",
    "vacancy",
    "developer",
    "desarrollador",
    "desarrolladora",
    "engineer",
    "ingeniero",
    "ingeniera",
    "remote",
    "remoto",
    "híbrido",
    "hybrid",
    "opportunity",
    "oportunidad laboral",
    "we are looking for",
    "buscamos",
    "estamos buscando",
    "join our team",
    "únete a nuestro equipo",
    "recruiter",
    "reclutador",
    "reclutadora",
    "apply now",
    "postulate",
    "postúlate",
    "postula",
    "cv al correo",
    "envianos tu cv",
    "resume to",
    "linkedin.com/jobs",
    "software engineer",
    "backend",
    "frontend",
    "full stack",
    "fullstack",
    # Tecnologías/categorías cerradas del Analyzer (ver ROADMAP.md Fase 3)
    "python",
    "java",
    "javascript",
    "typescript",
    "node.js",
    "nodejs",
    "golang",
    " go dev",
    "elixir",
    "phoenix framework",
    "machine learning",
    "deep learning",
    "artificial intelligence",
    "inteligencia artificial",
    " ai ",
    " ai/ml",
    "llm",
    "data scientist",
)


def is_candidate_job_post(content: str) -> bool:
    """Returns `True` if `content` looks cheap-and-plausibly like a tech job post.

    Descarta, sin costo de LLM:
    - posts triviales por longitud (`len(content) < 40`, ver
      `TOKEN_OPTIMIZATION.md` §2);
    - posts que no contienen ninguna keyword conocida de empleo/tech.

    No es una decisión final de `NOT_RELEVANT`: solo evita gastar tokens en
    lo obviamente irrelevante. Todo lo que pasa este filtro igual pasa por
    `LLMProvider.analyze_job()`, que es quien decide `is_job` con más
    contexto que un simple match de keywords.
    """
    if len(content) < _MIN_CONTENT_LENGTH:
        return False

    normalized = content.lower()
    return any(keyword in normalized for keyword in _JOB_KEYWORDS)
