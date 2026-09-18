# Clean Architecture Skeleton

Este documento es la referencia viva (no un ADR puntual) del árbol de carpetas por capas
definido para la Fase 0. Si cambia, actualízalo aquí y, si el cambio es significativo,
documenta el porqué en un nuevo ADR bajo `docs/decisions/`.

Fuente normativa: [`ENGINEERING_STANDARDS.md`](../../ENGINEERING_STANDARDS.md) §1, §26;
ownership por agente: [`docs/agents/AGENTS.md`](../agents/AGENTS.md) §7-14, §18.

## Regla de dependencia (no negociable)

```
Presentation → Application → Domain
Infrastructure → Application interfaces → Domain
```

El Domain nunca importa FastAPI, SQLAlchemy, Playwright, Gmail SDK, OpenAI/Anthropic SDK,
HTTP clients ni filesystem. Infrastructure implementa Protocols definidos en Domain/Application;
nunca al revés.

## Árbol de carpetas (Fase 0 — solo estructura, sin lógica de negocio)

```
app/
├── __init__.py
├── main.py                          # composition root (stub en Fase 0; se completa en Fase 6/9)
│
├── domain/
│   ├── __init__.py
│   ├── entities/                    # Job, JobAnalysis, Application, CVProfile, EmailDraft
│   ├── value_objects/                # JobId, ApplicationId, CVId, EmailAddress
│   ├── repositories/                 # Protocols de persistencia de entidades propias:
│   │                                  #   JobRepository (incl. list_by_status, ver ADR-004),
│   │                                  #   JobAnalysisRepository, ApplicationRepository (ADR-004)
│   ├── services/                     # domain services sin estado, reglas puras multi-entidad
│   └── exceptions/                   # CVNotFoundError, InvalidStateTransitionError, etc.
│
├── application/
│   ├── __init__.py
│   ├── use_cases/                    # CollectFeedPosts, AnalyzeJobPost, SelectBestCV,
│   │                                  #   GenerateApplicationEmail, CreateGmailDraft, IgnoreJob...
│   ├── dto/                          # JobResponse, ApplicationResponse, CreateDraftRequest,
│   │                                  #   RawFeedPost (ver ADR-003), JobAnalysisResult,
│   │                                  #   EmailContext, GeneratedEmail (ver ADR-005)...
│   ├── interfaces/                   # Protocols de dependencias EXTERNAS al propio storage de
│   │                                  #   entidades, consumidas por use cases de backend-engineer:
│   │                                  #   LLMProvider (ver ADR-005), FeedCollector (ver ADR-003),
│   │                                  #   EmailDraftRepository; llm_provider_errors.py
│   │                                  #   (LLMProviderError, LLMResponseValidationError — ADR-006)
│   └── cv/                           # ownership: cv-matching-agent (AGENTS.md §11)
│                                      #   lógica de matching determinístico (matcher.py),
│                                      #   normalización de skills, y el Protocol
│                                      #   CVCatalogRepository (interfaces.py) — vive aquí, no en
│                                      #   application/interfaces/, porque su único consumidor
│                                      #   (matcher.py) y su implementación (infrastructure/cv/)
│                                      #   son ambos ownership de cv-matching-agent; sin SDKs externos;
│                                      #   cv_catalog_errors.py (CVInfrastructureError — ADR-006)
│
├── infrastructure/
│   ├── __init__.py
│   ├── database/
│   │   ├── repositories/             # SQLAlchemyJobRepository, etc. (implementan domain/repositories)
│   │   # sqlalchemy_models.py, session.py se agregan en Fase 1 (database-agent)
│   ├── linkedin/                     # browser.py, session.py, feed.py, parser.py,
│                                      #   linkedin_feed_collector.py (Fase 2, linkedin-agent)
│   ├── llm/                          # openai_provider.py, anthropic_provider.py, exceptions.py
│   │                                  #   (Fase 3/5, llm-agent — ver ADR-005)
│   ├── gmail/                        # gmail_client.py, gmail_draft_repository.py (Fase 7, gmail-agent)
│   ├── cv/                           # filesystem_cv_repository.py (Fase 4, cv-matching-agent)
│   └── config/                       # Settings tipado (pydantic-settings), lee .env
│
└── presentation/
    ├── __init__.py
    ├── api/
    │   └── routes/                   # jobs.py, applications.py, cvs.py (Fase 6, backend-engineer)
    └── scheduler/                    # jobs.py con APScheduler (Fase 9, backend-engineer)

tests/
├── unit/
├── integration/
└── e2e/

prompts/
├── job-analysis/
├── cv-matching/
└── email-generation/

cvs/                                  # PDFs reales, gitignored (contenido); carpeta versionada vía .gitkeep

migrations/                           # Alembic (Fase 1, database-agent)

docs/
├── architecture/                     # este documento y futuros (ya existía docs/agents, docs/optimization)
└── decisions/                        # ADRs (001, 002, ...)
```

## Notas de diseño

1. **`domain/repositories/` vs `application/interfaces/` vs `application/cv/`**: se separan
   deliberadamente tres ubicaciones para Protocols, según quién los consume y quién los implementa:
   - `domain/repositories/`: contratos de persistencia de las propias entidades de dominio
     (`JobRepository.save`, `find_by_hash`, `find_pending`) — lenguaje de dominio, sin detalles
     de infraestructura, alineado con ENGINEERING_STANDARDS §1 y §9.
   - `application/interfaces/`: contratos hacia sistemas externos que un use case de
     **backend-engineer** orquesta y que no son "nuestra" persistencia (`LLMProvider`,
     `FeedCollector`, `EmailDraftRepository`). `EmailDraftRepository` se llama "Repository" por
     convención de AGENTS.md/ENGINEERING_STANDARDS, pero conceptualmente es un puerto hacia Gmail,
     no persistencia de una entidad propia — vive en `application/interfaces/`, no en
     `domain/repositories/`.
   - `application/cv/`: el Protocol `CVCatalogRepository` vive dentro del propio subpaquete de
     `cv-matching-agent` (junto a `matcher.py`), **no** en `application/interfaces/`, porque tanto
     su único consumidor (`matcher.py`) como su implementación (`infrastructure/cv/`) son
     ownership exclusivo de `cv-matching-agent` (AGENTS.md §11/§18). Ponerlo en
     `application/interfaces/` (ownership de backend-engineer) obligaría a `cv-matching-agent` a
     editar carpetas fuera de su ownership cada vez que necesite cambiar ese contrato — la regla
     de criterio es: si el mismo agente posee el consumidor y la implementación de un Protocol,
     el Protocol vive junto al consumidor, no en el paquete compartido de interfaces.
   Todos son Protocols sin imports de frameworks; la diferencia es de intención semántica y de
   ownership, no de mecanismo. Ver ADR-001 para alternativas consideradas.

   **Extensión a excepciones (ADR-006):** el mismo criterio de ownership aplica a las excepciones
   que documentan el contrato informal ("raises") de un Protocol — Python no tiene `raises`
   tipado, así que ese contrato vive solo en el docstring del Protocol y en la ubicación de la
   propia excepción. Una excepción que `application/` importa y captura por nombre (no toda su
   jerarquía — solo lo que se importa hoy, YAGNI) se mueve junto al Protocol al que pertenece
   semánticamente, no junto al use case que hoy la captura: `LLMProviderError`/
   `LLMResponseValidationError` viven en `application/interfaces/llm_provider_errors.py` (junto a
   `LLMProvider`, ownership `backend-engineer`); `CVInfrastructureError` (solo la raíz) vive en
   `application/cv/cv_catalog_errors.py` (junto a `CVCatalog`, ownership `cv-matching-agent`),
   aunque hoy la capture un use case de `backend-engineer` (`GenerateApplicationEmail`) — mismo
   principio que `JobRepository` no se muda de `domain/repositories/` solo porque use cases de
   otros agentes lo consuman. Las subclases concretas de infraestructura que ningún módulo de
   `application/` importa por nombre (p. ej. `CVCatalogNotFoundError`, `CVFileNotFoundError`) se
   quedan en `infrastructure/`, heredando de la clase movida en vez de la raíz local que se retira.

   **Nota de consistencia documental (deuda técnica pendiente, detectada por `code-reviewer` en la
   revisión de ADR-006, no resuelta por este documento):** el párrafo de arriba y la tabla de
   ownership siguen llamando al Protocol de CV `CVCatalogRepository` (en un archivo `interfaces.py`),
   nombre heredado literalmente de `ADR-001` punto 3. El código real
   (`app/application/cv/cv_catalog.py`) usa `class CVCatalog(Protocol)` — el rename ocurrió en
   algún momento posterior a ADR-001 sin que ningún ADR lo registrara. Mismo tratamiento que la
   "Nota de consistencia documental" de la nota 7 para `FeedCollector`/`JobPost`: se deja registrado
   acá en vez de corregirse en silencio; `orchestrator` decide si actualiza `ADR-001` y el resto de
   este documento para usar `CVCatalog` de forma consistente.

2. **`application/services/` y `application/commands/`** (propuestos en ENGINEERING_STANDARDS §1)
   se omiten deliberadamente en la Fase 0. Ningún caso de uso documentado en ROADMAP.md requiere
   hoy un "application service" separado de un use case, ni un patrón Command explícito — agregarlos
   ahora sería premature abstraction (ENGINEERING_STANDARDS §31, §34). Si en una fase posterior
   aparece lógica compartida entre múltiples use cases que no encaja como entidad/servicio de
   dominio, se crea `application/services/` en ese momento (extensión no disruptiva, no requiere
   mover código existente de otras carpetas).

3. **`config/` a nivel raíz** (propuesto en la estructura plana original de CLAUDE.md, ej.
   `config/cvs.yaml`) no se agrega en la Fase 0 porque ENGINEERING_STANDARDS §26 (que reemplaza
   la estructura plana) no lo incluye. La ubicación final de `cvs.yaml`
   (`config/cvs.yaml` vs `app/infrastructure/cv/cvs.yaml`) queda como decisión del
   `cv-matching-agent` en la Fase 4 — no se resuelve aquí para no invadir su ownership con una
   decisión prematura.

4. **`prompts/cv-matching/`** se crea vacía en la Fase 0 por seguridad estructural
   (ENGINEERING_STANDARDS §16 la menciona), aunque ROADMAP Fase 4 deja el matching determinístico
   como default y el fallback LLM como opcional — puede quedar sin `v1.txt` hasta que
   `cv-matching-agent` lo necesite.

5. Todas las carpetas de la Fase 0 se crean **vacías** (sin lógica de negocio), con `.gitkeep`
   donde no haya `__init__.py`, para que git las trackee. El orchestrator decide el mecanismo
   exacto (`__init__.py` vs `.gitkeep`) al crear los archivos.

6. **`FeedCollector` (`application/interfaces/feed_collector.py`) y `RawFeedPost`
   (`application/dto/raw_feed_post.py`)** — ver ADR-003 para la decisión completa. Resumen:
   `FeedCollector.collect() -> list[RawFeedPost]` — **no** `list[Job]`. `RawFeedPost` es un DTO
   plano (sin validación de invariantes) que `linkedin-agent` construye en
   `app/infrastructure/linkedin/linkedin_feed_collector.py`; es `CollectFeedPosts`
   (`app/application/use_cases/`, ownership `backend-engineer`) quien lo traduce a `Job` vía
   `Job.create(...)` y orquesta la deduplicación contra `JobRepository.get_by_content_hash`.
   `linkedin-agent` nunca importa `Job` ni `JobRepository` — su única responsabilidad es devolver
   datos estructurados (consistente con `.claude/agents/05_linkedin.md`). La whitelist de URLs
   (`linkedin.com/feed/*`) y la excepción específica ante navegación no permitida son detalles de
   implementación de `linkedin-agent`, no parte del Protocol. ADR-003 también corrige la lectura
   literal de `ROADMAP.md` Fase 2 punto 5: `linkedin_feed_collector.py` calcula `content_hash`,
   pero no llama a `JobRepository` directamente — eso vive en `CollectFeedPosts`.

7. **Repositorios de `JobAnalysis`/`Application` y `LLMProvider` (Fases 3-6)** — ver ADR-004 y
   ADR-005 para la decisión completa. Resumen:
   - `JobRepository` gana `list_by_status(status, *, limit, offset) -> list[Job]` (ADR-004 §1),
     firma reutilizada por Analyzer (Fase 3), CV Matcher (Fase 4) y el dashboard (Fase 6) — ningún
     otro agente debe inventar un nombre distinto para "listar jobs por estado".
   - `JobAnalysisRepository` (`domain/repositories/job_analysis_repository.py`,
     `save`/`get_by_job_id`) y `ApplicationRepository`
     (`domain/repositories/application_repository.py`, `save`/`get_by_id`/`get_by_job_id`) son
     Protocols propios, un archivo por entidad, mismo criterio que `JobRepository` (ADR-004 §2-3).
   - **Corrección a `ROADMAP.md` Fase 5 punto 3 / Fase 6 punto 3 (ADR-004 §3):**
     `ApplicationRepository` no se usa hasta Fase 7 (`CreateGmailDraft`) — la entidad `Application`
     ya implementada solo puede crearse en `DRAFT_CREATED` (requiere un `gmail_draft_id` real), sin
     estado intermedio para "email generado, draft pendiente". Fase 5 (Email Generator) y la edición
     manual de Fase 6 persisten `subject`/`body` en `JobAnalysis` (vía `JobAnalysisRepository`), no
     en `applications`.
   - `LLMProvider` (`application/interfaces/llm_provider.py`): `analyze_job(content: str) ->
     JobAnalysisResult` y `generate_email(context: EmailContext) -> GeneratedEmail` — ambos
     devuelven DTOs planos (`application/dto/`), no entidades de dominio directamente, mismo
     criterio que `FeedCollector`/`RawFeedPost` (ADR-003). `EmailContext.cv_summary: str` (nunca una
     ruta a PDF, por `TOKEN_OPTIMIZATION.md` §7).
   - **Actualizado por ADR-006:** `LLMProviderError`/`LLMResponseValidationError` — las dos
     excepciones que `AnalyzeJobPost`/`GenerateApplicationEmail` capturan explícitamente por
     nombre — viven en `application/interfaces/llm_provider_errors.py`, no en
     `infrastructure/llm/exceptions.py` (violaría el dependency rule). ADR-006 dejaba a criterio de
     `llm-agent` conservar ahí una raíz local `LLMInfrastructureError` u optar por retirarla
     (YAGNI, sin subclases reales una vez que ambas heredan del archivo nuevo); `llm-agent` la retiró
     -- `infrastructure/llm/exceptions.py` ya no define ninguna raíz local, solo hereda sus dos
     clases directamente desde el archivo nuevo (ver su propio docstring). Mismo criterio para
     `CVInfrastructureError` (solo la raíz, no sus subclases concretas): vive en
     `application/cv/cv_catalog_errors.py`, junto al Protocol `CVCatalog` al que pertenece, no en
     `application/interfaces/` — ver ADR-006 para el criterio general de ownership de excepciones.

   **Deuda técnica cross-agente identificada al escribir ADR-005 (no resuelta aquí, pendiente de
   `domain-engineer`):** `Job` no tiene ningún método para persistir el email extraído por el
   Analyzer después de `Job.create(...)` (`DEBT-ADR005-01`); `JobAnalysis` no tiene campos
   estructurados `company`/`role_title` para el Email Generator (`DEBT-ADR005-02`, mitigado
   pasando el `content` original del post como parte de `EmailContext`). Ver ADR-005, sección
   "Risks", para el detalle y la solución recomendada.

   **Nota de consistencia documental (deuda técnica pendiente, no resuelta por este documento):**
   `ENGINEERING_STANDARDS.md` §4 y §14, `.claude/agents/05_linkedin.md` (sección "Interfaz
   esperada") y `.claude/agents/03_domain.md` todavía muestran una firma anterior,
   `FeedCollector.collect() -> list[JobPost]`, con `JobPost` como entidad de dominio separada de
   `Job`. Esa firma quedó obsoleta cuando Fase 1 consolidó todo en una única entidad `Job`
   (`app/domain/entities/job.py`), pero ningún ADR anterior dejó registrada esa consolidación, y
   esos tres documentos —normativos, citados directamente por `linkedin-agent` como referencia de
   primera mano— no se corrigieron en consecuencia. Este documento y ADR-003 son la fuente de
   verdad vigente (`list[RawFeedPost]`, sin `JobPost`); `ENGINEERING_STANDARDS.md` y
   `.claude/agents/*.md` no son ownership de `architect` (ver `docs/agents/AGENTS.md`), así que no
   se editan aquí — queda como tarea pendiente para `orchestrator`, reportada explícitamente como
   deuda técnica (ver ADR-003, sección Risks).

## Mapeo ownership → carpeta (resumen, ver AGENTS.md para el detalle completo)

| Carpeta | Agente owner |
|---|---|
| `app/domain/**` | domain-engineer |
| `app/application/**` (excepto `cv/`) | backend-engineer |
| `app/application/cv/**` (incluye `CVCatalogRepository`) | cv-matching-agent |
| `app/presentation/**` | backend-engineer |
| `app/main.py` (composition root / DI) | backend-engineer |
| `app/infrastructure/database/**`, `migrations/**` | database-agent |
| `app/infrastructure/linkedin/**` | linkedin-agent |
| `app/infrastructure/llm/**`, `prompts/**` | llm-agent (co-ownership con token-optimization-agent, ver AGENTS.md §36) |
| `app/infrastructure/gmail/**` | gmail-agent |
| `app/infrastructure/cv/**`, `cvs/**` | cv-matching-agent |
| `app/infrastructure/config/**` | backend-engineer — decisión explícita de este ADR: AGENTS.md no asigna owner a esta carpeta; se asigna a backend-engineer porque ya compone `main.py`/el DI root y consume `Settings` desde `app/presentation/**`; si otro agente (p. ej. database-agent) necesita agregar campos de configuración propios (`DATABASE_URL`, etc.), lo hace coordinando con backend-engineer en vez de asumir ownership propio |
| `frontend/**` | frontend-agent |
| `tests/**` | testing-agent |
| `docs/architecture/**`, `docs/decisions/**` | architect |
