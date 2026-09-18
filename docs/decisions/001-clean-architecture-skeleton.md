# ADR-001: Clean Architecture folder skeleton for Fase 0

## Status

Accepted.

## Problem

El repositorio solo tiene documentación (`CLAUDE.md`, `ENGINEERING_STANDARDS.md`, `ROADMAP.md`,
`docs/agents/AGENTS.md`, `.claude/agents/*.md`). No existe código todavía. Antes de que el
orchestrator cree carpetas y archivos masivamente (Fase 0), se necesita fijar un único árbol de
carpetas por capas, consistente con:

- `ENGINEERING_STANDARDS.md` §1 (propuesta de capas) y §26 (repository structure).
- `docs/agents/AGENTS.md` §7-14 (ownership por agente) y §18 (mapeo agente → carpeta).
- `CLAUDE.md` (nota explícita: la estructura por capas reemplaza la estructura plana `app/linkedin/`,
  `app/jobs/`, etc. descrita más arriba en ese mismo archivo).
- `ROADMAP.md` Fase 0-9, que referencia rutas concretas (`app/domain/entities/`,
  `app/infrastructure/linkedin/feed.py`, `app/application/cv/matcher.py`, etc.).

Sin este documento, cada agente especializado podría crear su propia interpretación de dónde
vive cada cosa (p. ej. dónde van los Protocols de LLM, o si `application/cv/` existe o no),
generando imports circulares o violaciones del dependency rule desde el primer commit.

## Current approach

Ninguno — no hay código. Solo existen tres propuestas de estructura ligeramente distintas y no
reconciliadas explícitamente en un único documento:

1. La estructura plana original en `CLAUDE.md` (`app/linkedin/`, `app/jobs/`, `app/cv/`, `app/ai/`,
   `app/gmail/`, `app/database/`, `app/scheduler/`) — explícitamente reemplazada por la de capas.
2. La propuesta de capas en `ENGINEERING_STANDARDS.md` §1 y §26
   (`domain/{entities,value_objects,repositories,services,exceptions}`,
   `application/{use_cases,dto,commands,services}`,
   `infrastructure/{database,linkedin,gmail,llm,cv,config}`, `presentation/{api,web}`).
3. Las rutas concretas que `ROADMAP.md` y `docs/agents/AGENTS.md` ya asumen como existentes
   (`app/application/cv/`, `app/application/dto/`, `app/domain/repositories/`,
   `app/presentation/scheduler/`), que no coinciden 1:1 con la propuesta (2) — por ejemplo,
   `ENGINEERING_STANDARDS.md` no menciona `application/cv/` ni `application/interfaces/`
   explícitamente, pero `AGENTS.md` §11 sí asigna `app/application/cv/**` como ownership del
   cv-matching-agent.

## Proposed approach

Fijar el árbol de carpetas documentado en
[`docs/architecture/clean-architecture-skeleton.md`](../architecture/clean-architecture-skeleton.md),
que reconcilia las tres fuentes:

```
app/
├── domain/{entities,value_objects,repositories,services,exceptions}/
├── application/{use_cases,dto,interfaces,cv}/
├── infrastructure/{database/repositories,linkedin,llm,gmail,cv,config}/
└── presentation/{api/routes,scheduler}/

tests/{unit,integration,e2e}/
prompts/{job-analysis,cv-matching,email-generation}/
cvs/
migrations/
docs/{architecture,decisions}/  (agents/ y optimization/ ya existían)
```

Decisiones concretas dentro de esta reconciliación:

1. **`application/interfaces/` se agrega** (no está en ENGINEERING_STANDARDS §1 literal, pero es
   necesaria): es donde viven los Protocols que los use cases de **backend-engineer** consumen y
   que Infrastructure implementa para servicios externos que no son persistencia de entidades
   propias — `LLMProvider`, `FeedCollector`, `EmailDraftRepository`. Esto es consistente con la
   regla de dependencia explícita en `docs/agents/AGENTS.md` §26:
   `Infrastructure → Application interfaces → Domain` — el propio texto de AGENTS.md nombra
   "Application interfaces" como concepto, así que necesita una carpeta.

   **Corrección post-review (code-reviewer, ver hallazgo #1):** `CVCatalogRepository` se excluye
   deliberadamente de esta carpeta y vive en `application/cv/` en su lugar — ver punto 3 más abajo.
   El criterio "va en `application/interfaces/`" solo aplica cuando el consumidor del Protocol es
   un use case de `backend-engineer`; cuando el consumidor y la implementación pertenecen ambos a
   otro agente con ownership propio de un subpaquete de `application/` (como `cv-matching-agent`
   con `application/cv/`), el Protocol vive junto a ese consumidor, no en el paquete compartido de
   interfaces — de lo contrario `cv-matching-agent` tendría que modificar una carpeta fuera de su
   ownership para cambiar el contrato de su propio catálogo, violando AGENTS.md §18.

2. **`domain/repositories/` se mantiene** para los Protocols de persistencia de entidades propias
   (`JobRepository`, `ApplicationRepository`) — sigue literalmente ENGINEERING_STANDARDS §1 y el
   ejemplo de §9. Ver nota de diseño 1 en el documento de arquitectura para el criterio de cuándo
   algo va en `domain/repositories/` vs `application/interfaces/`.

3. **`application/cv/` se agrega** explícitamente porque `AGENTS.md` §11 ya lo define como
   ownership del cv-matching-agent y `ROADMAP.md` Fase 4 referencia `app/application/cv/matcher.py`.
   No está en la propuesta original de ENGINEERING_STANDARDS §1, pero no la contradice — es una
   extensión necesaria para que el matching determinístico (que es lógica de aplicación, no de
   infraestructura ni de dominio puro) tenga un hogar. El Protocol `CVCatalogRepository` (que
   `filesystem_cv_repository.py` en `infrastructure/cv/` implementa) también vive aquí
   (`application/cv/interfaces.py`), no en `application/interfaces/`, porque `cv-matching-agent`
   posee de punta a punta tanto el consumidor como la implementación de ese contrato.

4. **Ownership de `infrastructure/config/**` se asigna explícitamente a `backend-engineer`**
   (corrección post-review, hallazgo #2): `docs/agents/AGENTS.md` §18 no asigna owner a esta
   carpeta — es un vacío real en la fuente normativa. Se resuelve, no se difiere, porque el
   `Settings` tipado (pydantic-settings) se necesita ya desde la Fase 1 (`DATABASE_URL` para
   database-agent) y dejarlo con doble owner potencial ("database-agent o backend-engineer") habría
   arriesgado que ningún agente lo cree o que se duplique. Se elige `backend-engineer` porque ya es
   quien compone `app/main.py` (composition root / DI, ver punto 5) y consume `Settings` desde
   `app/presentation/**`. Otros agentes que necesiten agregar campos de configuración propios
   (`database-agent` para `DATABASE_URL`, `llm-agent` para `OPENAI_API_KEY`, etc.) coordinan con
   `backend-engineer` en vez de asumir ownership de la carpeta.

5. **Ownership de `app/main.py`** (composition root) se asigna explícitamente a `backend-engineer`
   (corrección post-review, hallazgo #3) — coherente con que ya posee `application/**` y
   `presentation/**`, las dos capas que `main.py` conecta vía inyección de dependencias.

6. **`application/commands/` y `application/services/` se omiten** en la Fase 0 (ver nota de
   diseño 2 en el documento de arquitectura) — YAGNI, se agregan cuando haya un caso de uso real
   que los necesite.

7. **`infrastructure/config/`** aloja el `Settings` tipado (pydantic-settings) que lee `.env`,
   siguiendo ENGINEERING_STANDARDS §12 (ownership: `backend-engineer`, ver punto 4).

8. Todo se crea **vacío**, sin lógica de negocio — el objetivo de la Fase 0 es que el dependency
   rule sea imposible de violar por accidente porque las carpetas correctas ya existen antes de
   que cualquier agente empiece a escribir código.

## Alternatives

### A. Seguir ENGINEERING_STANDARDS §1 literal, sin `application/interfaces/` ni `application/cv/`

Poner los Protocols de servicios externos (`LLMProvider`, `FeedCollector`) directamente en
`domain/services/` o `domain/repositories/`, y la lógica de CV matching en
`application/services/cv_matcher.py` en vez de un subpaquete propio.

- **Trade-off:** más apegado al texto literal de ENGINEERING_STANDARDS §1, pero contradice
  `AGENTS.md` §11 y §18 (que ya fijan `app/application/cv/**` como ownership) y `ROADMAP.md`
  Fase 4. Meter Protocols de infraestructura externa (LLM, feed scraping) dentro de
  `domain/services/` difumina la frontera entre "regla de negocio pura" y "contrato hacia un
  sistema externo", lo cual complica el dependency rule en vez de aclararlo.
- **Rechazada** porque generaría inconsistencia inmediata entre el skeleton y la documentación de
  agentes ya existente y aprobada.

### B. Un único paquete `interfaces/` a nivel raíz de `app/`, compartido por domain y application

En vez de separar `domain/repositories/` de `application/interfaces/`, tener un solo
`app/interfaces/` con todos los Protocols (de entidades y de servicios externos).

- **Trade-off:** más simple (una sola carpeta que buscar), pero pierde la señal semántica de qué
  Protocol pertenece a qué capa, y no calza con la carpeta `domain/repositories/` que
  ENGINEERING_STANDARDS §1 ya define explícitamente en el árbol propuesto.
- **Rechazada** — preferible mantener la separación semántica ya que es de bajo costo (dos
  carpetas en vez de una) y evita tener que decidir caso por caso si algo "es de dominio o no" al
  momento de importarlo.

### C. Crear todas las subcarpetas de ENGINEERING_STANDARDS §1 tal cual (incluyendo `commands/`,
`services/` en application) por completitud, aunque queden vacías indefinidamente

- **Trade-off:** máxima fidelidad al documento fuente, pero carpetas vacías sin ningún archivo
  planeado en el ROADMAP invitan a que alguien meta código ahí "porque existe la carpeta",
  violando YAGNI y el principio de simplicidad (ENGINEERING_STANDARDS §31, "No Overengineering").
- **Rechazada** — se prefiere agregar la carpeta cuando aparezca el primer caso de uso real
  (costo de agregar una carpeta después es cero; costo de tener carpetas fantasma que acumulan
  código no planeado es real).

## Trade-offs

- Reconciliar tres fuentes (CLAUDE.md, ENGINEERING_STANDARDS.md, AGENTS.md/ROADMAP.md) en vez de
  seguir una sola al pie de la letra introduce una capa de interpretación del architect — se
  documenta explícitamente aquí para que sea auditable y no una decisión implícita.
- Separar `domain/repositories/` de `application/interfaces/` agrega una decisión de juicio
  (¿esto es persistencia propia o puerto externo?) que los agentes deberán aplicar caso por caso;
  se mitiga con los ejemplos concretos en la nota de diseño 1 del documento de arquitectura.
- Omitir `application/commands/` y `application/services/` ahora significa que si aparece la
  necesidad en Fase 3+, el backend-engineer deberá crear la carpeta como parte de esa tarea (no es
  gratis, pero es una operación aditiva y de bajo riesgo).

## Risks

- **Riesgo bajo:** un agente futuro podría no encontrar un Protocol donde lo esperaba (domain vs
  application) — mitigado documentando el criterio explícitamente y con ejemplos concretos.
- **Riesgo bajo:** si más adelante se decide que `EmailDraftRepository` sí debería vivir en
  `domain/repositories/` (por ejemplo, si se modela `EmailDraft` como una entidad de dominio con
  persistencia propia en vez de un puerto hacia Gmail), habría que mover un archivo y sus imports.
  Bajo costo porque en Fase 0 no hay código, y en fases posteriores es un solo archivo con pocos
  consumidores (use cases).
- **Riesgo nulo de violar el dependency rule por este ADR en sí** — el skeleton no agrega ninguna
  dependencia entre capas, solo nombra carpetas vacías.

## Migration impact

Ninguno todavía — no existe código previo que migrar. Este ADR es la línea base. Cualquier
desviación futura de este árbol (mover una carpeta, fusionar `domain/repositories/` con
`application/interfaces/`, agregar `application/services/`) debe documentarse como un nuevo ADR
que referencie este, en vez de cambiarse silenciosamente.
