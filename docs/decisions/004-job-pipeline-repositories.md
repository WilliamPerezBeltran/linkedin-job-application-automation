# ADR-004: Repository boundaries for `JobAnalysis` and `Application`, and `JobRepository` extension for Fases 3-6

## Status

Accepted.

## Problem

Fase 2 (LinkedIn Collector) cierra con un único repositorio de dominio,
`JobRepository` (`app/domain/repositories/job_repository.py`), con el alcance
deliberadamente mínimo que su propio docstring fija: `save`, `get_by_id`,
`get_by_content_hash`, y una nota explícita — *"Métodos adicionales
(`list_by_status`, etc.) se agregan en fases posteriores cuando un use case
concreto los necesite (YAGNI)"*.

Fases 3-6 (`ROADMAP.md`) introducen tres necesidades de persistencia nuevas
que ese alcance mínimo no cubre, y que si no se deciden ahora, cada agente
las resolverá de forma distinta e incompatible:

1. **`llm-agent`** (Fase 3, `AnalyzeJobPost`) necesita listar todos los `Job`
   en `SCRAPED` para procesarlos en batch.
2. **`cv-matching-agent`** (Fase 4, `SelectBestCV`) necesita listar todos los
   `Job` en `RELEVANT`.
3. **`backend-engineer`** (Fase 6, `GET /api/jobs`) necesita lo mismo con
   filtro por status + paginación, expuesto vía API.
4. **`JobAnalysis`** (entidad ya implementada y estable, ownership
   `domain-engineer`, no se toca aquí) no tiene ningún repositorio propio
   todavía — solo existe `JobAnalysisModel` (SQLAlchemy) mapeando la tabla
   `job_analysis`, ya migrada 1:1 con `jobs.id` como PK/FK
   (`app/infrastructure/database/sqlalchemy_models.py`). Sin una decisión
   explícita, `llm-agent` (Fase 3), `cv-matching-agent` (Fase 4) y
   `llm-agent` de nuevo (Fase 5) podrían cada uno inventar su propia forma
   de persistir `JobAnalysis` (un método ad-hoc en `JobRepository`, un
   segundo Protocol con nombre distinto, o acceso directo a
   `JobAnalysisModel` saltándose el dependency rule).
5. **`Application`** (entidad ya implementada y estable, tampoco se toca
   aquí) tampoco tiene repositorio propio — solo `ApplicationModel`. El
   pedido original de esta tarea asumía que hace falta desde Fase 5 (Email
   Generator), citando `ROADMAP.md` Fase 5 punto 3 ("guarda en
   `job_analysis`/`applications`"). Este ADR corrige esa lectura (ver
   sección 3 de "Proposed approach") una vez confrontada con la forma real
   ya implementada de `Application.create()`.

`docs/agents/AGENTS.md` §19-20 exige explícitamente que el Orchestrator
evite que dos agentes redefinan el mismo concepto y que cada concepto de
dominio compartido (`Job`, `JobAnalysis`, `Application`) tenga una única
fuente de verdad — incluyendo, por extensión, una única forma de
persistirlo. Este ADR fija esa forma antes de que `llm-agent`,
`cv-matching-agent`, `database-agent` y `backend-engineer` empiecen a
implementar en paralelo.

## Current approach

- `app/domain/repositories/job_repository.py`: `JobRepository` (`Protocol`)
  con `save`, `get_by_id`, `get_by_content_hash` únicamente.
- `docs/architecture/clean-architecture-skeleton.md` ya menciona, en el
  comentario del árbol de carpetas (línea de `domain/repositories/`), los
  nombres `JobRepository, ApplicationRepository, JobAnalysisRepository`
  como contenido esperado de esa carpeta — una señal de intención previa,
  pero sin firma ni justificación fijada todavía; este ADR es el que la
  fija.
- No existe ningún Protocol para `JobAnalysis` ni para `Application`.
- `JobAnalysisModel`/`ApplicationModel` (SQLAlchemy) ya están migrados,
  1:1 con las entidades de dominio correspondientes (ver
  `app/infrastructure/database/sqlalchemy_models.py`).

## Proposed approach

### 1. `JobRepository`: agregar `list_by_status`

Se agrega un método a la interfaz ya existente:

```python
class JobRepository(Protocol):
    def save(self, job: Job) -> None: ...
    def get_by_id(self, job_id: JobId) -> Job | None: ...
    def get_by_content_hash(self, content_hash: str) -> Job | None: ...

    def list_by_status(self, status: JobStatus, *, limit: int = 50, offset: int = 0) -> list[Job]:
        """Returns Jobs currently in `status`, oldest-first, paginated."""
        ...
```

Decisiones concretas:

- **Un único método, reutilizado por los tres consumidores** (Analyzer,
  CV Matcher, dashboard) — no un método distinto por caso de uso
  (`list_scraped()`, `list_relevant()`, `list_for_dashboard(...)`). Los
  tres necesitan exactamente lo mismo: "jobs en un status dado, con límite y
  offset". Nombrar el parámetro `status` en vez de fijar el valor en el
  nombre del método evita que tres agentes distintos inventen tres firmas
  para el mismo concepto (`AGENTS.md` §19-20).
- **`limit`/`offset` con default (`50`/`0`)**, no obligatorios: el Analyzer y
  el CV Matcher (consumo interno del pipeline, sin UI) normalmente quieren
  "todo lo pendiente de este status" y pueden llamar con un `limit` alto
  explícito si su volumen lo requiere; el dashboard (Fase 6) sí necesita
  paginación real controlada por el usuario. Un único método con defaults
  razonables cubre ambos casos sin duplicar la interfaz.
- **Orden determinista, no especificado por parámetro:** el contrato exige
  que los resultados vuelvan en un orden estable y determinista (se sugiere
  `created_at` ascendente — procesar lo más antiguo primero, FIFO) para que
  la paginación del dashboard no repita/salte filas entre páginas si llegan
  jobs nuevos entre requests. La columna exacta de ordenamiento es detalle
  de implementación de `database-agent` (`SQLAlchemyJobRepository`); no se
  agrega un parámetro `order_by` al Protocol ahora (YAGNI — ningún caso de
  uso documentado necesita un orden distinto al default).
- **Se mantiene YAGNI para todo lo demás**: no se agrega `count_by_status`,
  `list_by_status_and_category`, ni filtros compuestos — se agregan cuando
  un caso de uso real los pida.

**Precedente vinculante para el resto del proyecto:** cualquier repositorio
futuro que necesite "listar por estado" (si `ApplicationRepository`
alguna vez lo necesitara, ver sección 3) debe reusar esta misma forma
(`list_by_status(status, *, limit, offset) -> list[Entity]`) en vez de
inventar un nombre distinto — se fija aquí explícitamente para que ningún
agente decida esto por su cuenta.

### 2. `JobAnalysisRepository`: Protocol propio

**Decisión: sí, un `JobAnalysisRepository` propio (`Protocol`), en
`app/domain/repositories/job_analysis_repository.py`.**

```python
class JobAnalysisRepository(Protocol):
    def save(self, job_analysis: JobAnalysis) -> None:
        """Persists a JobAnalysis (insert or update, keyed by job_id)."""
        ...

    def get_by_job_id(self, job_id: JobId) -> JobAnalysis | None: ...
```

Razonamiento (explícito, como pide la tarea, no asumido):

- **Coherencia con el patrón ya establecido.** `JobRepository` es "un
  Protocol por entidad/raíz de agregado con persistencia propia" — el
  mismo criterio que `docs/architecture/clean-architecture-skeleton.md`
  nota de diseño 1 ya fija para `domain/repositories/`
  ("contratos de persistencia de las propias entidades de dominio").
  `JobAnalysis` es una entidad de dominio standalone (no un value object
  embebido en `Job`): tiene sus propios métodos de negocio
  (`record_cv_recommendation`, `record_generated_email`) y su propio
  invariante (no se puede grabar `generated_email` sin `recommended_cv`).
  Envolverla en un método adicional de `JobRepository` (p. ej.
  `JobRepository.save_analysis(job, analysis)`) mezclaría en una misma
  interfaz la persistencia de dos entidades con ciclos de vida y
  responsabilidades distintas, violando SRP al nivel de la interfaz —
  exactamente el tipo de acoplamiento que `ENGINEERING_STANDARDS.md`
  (Interface Segregation) pide evitar.
- **Independencia de ownership entre fases.** `JobAnalysis` se escribe y
  reescribe por tres agentes distintos en tres fases distintas
  (`llm-agent` en Fase 3 y 5, `cv-matching-agent` en Fase 4). Si su
  persistencia colgara de `JobRepository` (ownership conceptual de
  `backend-engineer`/`domain-engineer` como "el" repositorio de jobs),
  cualquier cambio en cómo `JobAnalysis` se persiste forzaría tocar un
  archivo compartido por todos. Con un Protocol propio, cada agente
  depende únicamente del contrato de `JobAnalysisRepository`, no de
  `JobRepository`.
- **`get_by_job_id`, no un `id` propio**: `JobAnalysis` no tiene un
  identificador propio (se identifica por `job_id`, ver `__eq__`/`__hash__`
  de la entidad, y la tabla `job_analysis` usa `job_id` como PK) — el
  Protocol refleja exactamente esa relación 1:1, sin inventar un
  `JobAnalysisId` que no existe en el dominio.
- **`save` como upsert**, igual semántica que `JobRepository.save`: la
  primera llamada (Fase 3, Analyzer) inserta; las llamadas siguientes
  (Fase 4 CV Matcher, Fase 5 Email Generator) actualizan la misma fila vía
  `record_cv_recommendation`/`record_generated_email` sobre la instancia ya
  cargada con `get_by_job_id`. No se agrega `update` como método separado
  de `save` — mismo criterio ya usado por `JobRepository`.
- **No se agrega `list_by_*`** a `JobAnalysisRepository`: ningún caso de uso
  de Fase 3-6 necesita listar `JobAnalysis` de forma independiente de
  `Job` — el Analyzer/CV Matcher/dashboard siempre parten de
  `JobRepository.list_by_status(...)` y luego resuelven el análisis
  asociado con `get_by_job_id` por cada `Job`. Agregarlo ahora sería
  especulativo (YAGNI).

**Nota de consistencia transaccional (no resuelta aquí, deuda de diseño
documentada):** `AnalyzeJobPost` (Fase 3) necesita persistir un
`JobAnalysis` nuevo (`JobAnalysisRepository.save`) y mover el `status` del
`Job` correspondiente (`JobRepository.save`) en la misma operación lógica —
dos repositorios, dos llamadas a `save`. Con el driver síncrono ya decidido
en ADR-002 ("una `Session` por request/uso"), la forma correcta de mantener
esto atómico es que ambos repositorios concretos
(`SQLAlchemyJobRepository`, `SQLAlchemyJobAnalysisRepository`) compartan la
misma `Session` inyectada, y que el punto de commit sea uno solo (al final
del use case, o de la dependencia FastAPI que arma la `Session`) — **no**
que cada `Protocol.save()` haga su propio commit interno. Este ADR no
introduce un `UnitOfWork` explícito ahora (YAGNI — ADR-001 nota de diseño 2
ya aplicó el mismo criterio para `application/services`); si en fases
posteriores el número de repositorios que deben commitear juntos crece o
aparecen bugs de consistencia parcial, evaluar un ADR nuevo para introducir
`UnitOfWork`. `database-agent` debe documentar explícitamente, al
implementar Fase 3, en qué punto exacto se hace `session.commit()`.

### 3. `ApplicationRepository`: Protocol propio, pero necesario desde Fase 7, no Fase 5

**Decisión: sí, un `ApplicationRepository` propio (`Protocol`), en
`app/domain/repositories/application_repository.py`** — mismo
razonamiento que la sección 2 (entidad standalone con ciclo de vida propio,
ownership por fase distinto de `JobRepository`).

```python
class ApplicationRepository(Protocol):
    def save(self, application: Application) -> None: ...
    def get_by_id(self, application_id: ApplicationId) -> Application | None: ...
    def get_by_job_id(self, job_id: JobId) -> Application | None: ...
```

**Corrección explícita al pedido original de esta tarea y a
`ROADMAP.md` Fase 5 punto 3.** El pedido asumía que "el Email Generator ya
persiste algo en `applications` según `ROADMAP.md` punto 3 de Fase 5" y que
por tanto `ApplicationRepository` hace falta desde Fase 5. Confrontando eso
contra la entidad `Application` ya implementada
(`app/domain/entities/application.py`), **esa lectura es incompatible con
el código real**:

- `Application.create(...)` siempre arranca en `ApplicationStatus.DRAFT_CREATED`
  y su propio docstring es explícito sobre la intención de diseño:
  *"Refleja que un `Application` solo existe una vez que el draft de Gmail
  ya fue creado (Fase 7) — no hay un estado 'pendiente de draft' en esta
  entidad"*.
- No existe ningún estado anterior a `DRAFT_CREATED` en
  `ApplicationStatus` para representar "email generado, draft todavía no
  creado" — la máquina de estados de `Application` es únicamente
  `DRAFT_CREATED -> SENT`.
- **Precisión importante (detectada en revisión de `code-reviewer` sobre
  este mismo ADR):** esto no es una imposibilidad forzada por tipos. El
  `__init__` de `Application` solo valida `subject`/`body` no vacíos,
  `cv_path` sin segmentos `..`, y la consistencia `status`/`sent_at` — no
  existe ninguna validación que ate `gmail_draft_id` a `status`. Como
  `gmail_draft_id: str | None` es nullable, nada en el código actual impide
  en runtime llamar `Application.create(..., gmail_draft_id=None)` en
  Fase 5; no se lanzaría ninguna excepción. El argumento real para no
  hacerlo no es "no puede instanciarse", sino que hacerlo produce una fila
  semánticamente falsa — `status=DRAFT_CREATED` sin que exista un draft
  real en Gmail —, que Fase 7 (crear el draft real) y Fase 8
  (`mark_sent`, "Mark as Sent" sobre un draft que el usuario efectivamente
  revisó y envió) tratarían como si fuera cierta. Es una inconsistencia de
  *intención de diseño* documentada solo en el docstring de la entidad, no
  una invariante forzada por el constructor — mismo patrón, no forzado por
  tipos, ya aceptado en la sección 2 de este ADR ("documentado, no forzado
  por tipos") y en el tercer riesgo de "Risks" más abajo.
- Por lo tanto, **`Application` no debe instanciarse en Fase 5** — no
  porque el código lo impida, sino para no violar la intención de diseño ya
  fijada por `domain-engineer` (que este ADR tiene instrucción explícita de
  no rediseñar) y para no dejar el pipeline con estados falsos que las
  fases siguientes no pueden distinguir de un draft real.

**Resolución:** Fase 5 (Email Generator) persiste `subject`/`body`
**únicamente** en `JobAnalysis` (vía `JobAnalysis.record_generated_email` +
`JobAnalysisRepository.save`, ya cubierto por la sección 2), y mueve
`Job.status` a `EMAIL_GENERATED` — exactamente lo que ya permite el código
existente sin fricciones. **`ApplicationRepository` se necesita recién en
Fase 7** (`CreateGmailDraft`, `gmail-agent` + `backend-engineer`): ese use
case lee el `subject`/`body` ya generados desde `JobAnalysis`, el
`cv_path` desde `recommended_cv`, crea el draft real en Gmail (obteniendo
un `gmail_draft_id` real), y **recién ahí** llama
`Application.create(job_id=..., email=..., subject=..., body=...,
cv_path=..., gmail_draft_id=<el real>)` seguido de
`ApplicationRepository.save(application)` y `Job.mark_draft_created()`.

Esto también resuelve de forma consistente el punto 3 del pedido original
("Fase 6 necesita `Application` para el dashboard"): Fase 6 (dashboard) solo
necesita **leer** `JobAnalysis` (subject/body generado, editable) — no
necesita `Application` todavía, porque en Fase 6 ningún draft existe aún.
El flujo de edición manual de Fase 6 punto 3 (*"Permitir editar
manualmente el subject/body generado antes de crear el draft... guardar la
edición en `applications` vía la API"*) también debe leerse ajustado: la
edición se guarda actualizando `JobAnalysis` (`subject`/`generated_email`)
mientras el `status` siga en `EMAIL_GENERATED`, no en `applications` — la
tabla `applications` solo empieza a existir por fila una vez que Fase 7
crea el primer draft real. Se documenta como corrección explícita del texto
de `ROADMAP.md`, siguiendo el mismo criterio que ADR-003 usó para su
propia corrección de Fase 2 punto 5 — no se reescribe `ROADMAP.md` aquí
(fuera del ownership de `architect`); `orchestrator` decide si actualiza el
texto.

Decisiones de firma:

- **`get_by_job_id` devuelve `Application | None` (singular), no
  `list[Application]`.** El esquema SQL (`applications.job_id` sin
  `unique`) técnicamente permite más de una `Application` por `Job` —
  el propio docstring de `ApplicationModel` ya lo reconoce (*"un job puede
  tener más de un intento de aplicación... aunque el flujo actual del
  ROADMAP genera uno solo por job"*). Este ADR **mantiene esa ambigüedad
  documentada, no la resuelve**, porque resolverla implicaría o bien
  agregar una constraint `unique` a `applications.job_id` (cambio de
  esquema, ownership `database-agent`) o bien decidir semántica de "cuál
  aplicación es la vigente" (posible cambio de dominio, ownership
  `domain-engineer`) — ninguno de los dos es necesario para que Fase 7/8
  funcionen con el flujo actual de un intento por job. `get_by_job_id`
  queda documentado explícitamente con esta limitación: **su contrato
  asume el flujo actual de "como máximo una `Application` activa por
  `Job`"**; si una fase futura habilita reintentos múltiples por job, este
  método debe revisarse (renombrar a algo como `get_latest_by_job_id` o
  agregar `list_by_job_id`) en un ADR nuevo, no silenciosamente.
- **`get_by_id`** cubre Fase 8 (`POST /api/applications/{id}/mark-sent`),
  que necesita cargar una `Application` por su propio id antes de llamar
  `application.mark_sent(sent_at=...)`.
- **No se agrega `list_by_status`** todavía: `ROADMAP.md` Fase 8 punto 2
  ("vista de histórico/tablero") no fija un endpoint concreto ni su forma
  de filtrado — se agrega cuando `backend-engineer`/`frontend-agent` lo
  necesiten con una firma concreta en mano (YAGNI), reusando el mismo
  patrón `list_by_status(status, *, limit, offset)` fijado en la sección 1
  si aplica.

## Alternatives

### A. Un único `save` "agregado" en `JobRepository` que también persiste `JobAnalysis`/`Application`

P. ej. `JobRepository.save_with_analysis(job: Job, analysis: JobAnalysis | None) -> None`.

- **Trade-off:** resolvería la atomicidad transaccional de la sección 2 de
  forma más directa (un solo método, una sola `Session`, un solo commit
  implícito).
- **Rechazada:** viola SRP a nivel de interfaz (una misma clase persistiendo
  dos entidades con reglas de negocio distintas), acopla a
  `cv-matching-agent`/`llm-agent` con la interfaz "del job" en vez de con
  una propia de su concepto, y contradice el patrón ya usado en todo el
  proyecto (un Protocol por entidad con ciclo de vida propio). El problema
  de atomicidad real (sección 2) se resuelve mejor compartiendo `Session`
  entre repositorios concretos que fusionando sus contratos de dominio.

### B. Modelar `JobAnalysis` como parte del agregado `Job` (sin entidad standalone, sin repositorio propio)

- **Trade-off:** un solo repositorio para todo el pipeline de un job;
  coherente con DDD clásico si `JobAnalysis` no tuviera sentido fuera de
  `Job`.
- **Rechazada** porque ya no es una opción real: `JobAnalysis` **ya existe**
  como entidad standalone, con constructor propio, invariantes propias
  (`record_generated_email` exige `recommended_cv` previo) y
  `__eq__`/`__hash__` propios — es una decisión de `domain-engineer` ya
  tomada e implementada (Fase 1), fuera del alcance de este ADR
  rediseñarla.

### C. `Application` con repositorio propio pero necesario desde Fase 5 (aceptar el texto de `ROADMAP.md` literal, forzar un estado intermedio nuevo)

Agregar un estado nuevo a `ApplicationStatus` (p. ej. `EMAIL_GENERATED`) para
poder crear una `Application` en Fase 5 sin `gmail_draft_id`.

- **Trade-off:** seguiría el texto literal de `ROADMAP.md` Fase 5 punto 3
  sin necesidad de reinterpretarlo.
- **Rechazada** porque requiere modificar `ApplicationStatus` y
  `Application.create()`/`Application.__init__` (invariante
  `_require_status_sent_at_consistency`), es decir, tocar una entidad de
  dominio ya implementada y estable — explícitamente fuera de mi ownership
  en esta tarea ("no las rediseñes"). Además duplicaría información que ya
  vive en `JobAnalysis` (`subject`/`generated_email`) en dos tablas
  distintas antes de que el draft real exista, sin necesidad real.

## Trade-offs

| | Protocol propio por entidad (elegido) | Repositorio único "de pipeline" |
|---|---|---|
| Alineado con patrón ya usado (`JobRepository`) | Sí | No |
| Acoplamiento entre agentes de fases distintas | Bajo (cada uno depende solo de su Protocol) | Alto (todos dependen de un archivo compartido) |
| Atomicidad multi-repositorio | Requiere disciplina de `Session` compartida (documentado, no forzado por tipos) | Atomicidad "gratis" a nivel de firma |
| Riesgo de violar SRP de interfaz | Ninguno | Real |
| Fidelidad a `ROADMAP.md` Fase 5 punto 3 literal | No (se corrige explícitamente) | Sí, pero a costa de tocar dominio ya estable |

## Risks

- **Riesgo medio, ya mitigado por diseño explícito:** la atomicidad
  `Job` + `JobAnalysis` en `AnalyzeJobPost`/`SelectBestCV` depende de que
  `database-agent` comparta la misma `Session` entre
  `SQLAlchemyJobRepository` y `SQLAlchemyJobAnalysisRepository`, y de que
  el commit ocurra una sola vez por use case. Si `database-agent` crea una
  `Session` nueva por repositorio, un fallo a mitad de camino podría dejar
  `Job.status = ANALYZED` sin `JobAnalysis` persistido, o viceversa.
  Mitigación: este ADR deja la instrucción explícita (sección 2); si en la
  práctica esto genera bugs reales, evaluar `UnitOfWork` en un ADR
  posterior.
- **Riesgo bajo, deuda técnica documental:** `ROADMAP.md` Fase 5 punto 3 y
  Fase 6 punto 3 quedan corregidos por interpretación en este ADR
  (`applications` no se escribe hasta Fase 7), pero el texto del archivo no
  se edita aquí (fuera de ownership de `architect`). Si `llm-agent` o
  `backend-engineer` leen `ROADMAP.md` sin conocer este ADR, podrían
  intentar instanciar `Application` prematuramente y chocar con las
  invariantes ya implementadas de la entidad. Mitigación: `orchestrator`
  debe entregar este ADR explícitamente antes de que Fase 5 y Fase 6
  empiecen, igual que ya hizo con ADR-003 para Fase 2.
- **Riesgo bajo:** `get_by_job_id` de `ApplicationRepository` asume "a lo
  sumo una `Application` activa por `Job`", una simplificación documentada
  pero no forzada por una constraint de base de datos (`applications.job_id`
  no es `unique` en el esquema ya migrado). Si algún caller (por bug)
  inserta dos `Application` para el mismo `job_id`, el comportamiento de
  `get_by_job_id` ante esa fila duplicada queda sin definir por este ADR —
  se decide en la implementación de `database-agent` (p. ej. devolver la
  más reciente por `created_at`... nota: `Application` no tiene
  `created_at` propio en el esquema actual, solo `sent_at`; si esto se
  vuelve necesario, es una extensión de esquema para un ADR futuro, no de
  este).
- **Riesgo bajo, deuda técnica menor (detectada en revisión de
  `code-reviewer` sobre este mismo ADR):** la decisión de la sección 3 de
  no usar `ApplicationRepository` hasta Fase 7 depende de una intención de
  diseño documentada solo en el docstring de `Application.create()`, no de
  una invariante forzada por el constructor (`gmail_draft_id: str | None`
  es nullable y nada en `Application.__init__` impide en runtime crear una
  instancia con `status=DRAFT_CREATED` y `gmail_draft_id=None`). Si un
  agente futuro (o un bug en `CreateGmailDraft`) llama
  `Application.create(..., gmail_draft_id=None)` fuera de Fase 7, el
  dominio no lo rechaza — queda una fila indistinguible de un draft real
  para Fase 8 (`mark_sent`). **Solución recomendada (no forzada aquí):**
  `domain-engineer` podría agregar una guardia explícita en
  `Application.create()`/`__init__` (p. ej. exigir `gmail_draft_id` no
  `None` cuando `status is DRAFT_CREATED`, análogo a la guardia ya
  existente entre `status`/`sent_at`) en un ADR/cambio futuro si este riesgo
  se materializa en la práctica — no se agrega en este ADR porque excede mi
  ownership (`domain/entities/**`) y la entidad ya fue declarada estable
  para esta tarea.
- **Riesgo nulo de violar el dependency rule por este ADR en sí** — ambos
  Protocols nuevos viven en `app/domain/repositories/`, mismo criterio ya
  usado por `JobRepository`; ninguno importa SQLAlchemy ni ningún SDK
  externo.

## Migration impact

- `database-agent` debe implementar, en Fase 3 y Fase 7 respectivamente:
  - `SQLAlchemyJobRepository.list_by_status` (nuevo método sobre el
    repositorio ya existente).
  - `SQLAlchemyJobAnalysisRepository` (`app/infrastructure/database/repositories/`),
    implementando `JobAnalysisRepository`, mapeando desde/hacia
    `JobAnalysisModel` (ya migrado, sin cambios de esquema necesarios).
  - `SQLAlchemyApplicationRepository`, implementando `ApplicationRepository`,
    mapeando desde/hacia `ApplicationModel` (ya migrado, sin cambios de
    esquema necesarios) — recién consumido en Fase 7, no antes.
- `testing-agent` debe agregar, junto a cada Protocol nuevo, un fake
  in-memory (`InMemoryJobAnalysisRepository`, `InMemoryApplicationRepository`)
  siguiendo el mismo patrón que ya se espera de
  `InMemoryJobRepository` (ADR-003, Risks) — sin tocar PostgreSQL en tests
  unitarios de `AnalyzeJobPost`, `SelectBestCV`, `GenerateApplicationEmail`,
  `CreateGmailDraft`.
- `orchestrator` debe entregar este ADR a `llm-agent` (Fase 3 y 5),
  `cv-matching-agent` (Fase 4), `database-agent` y `backend-engineer` antes
  de que empiecen a implementar, y debe decidir si actualiza el texto de
  `ROADMAP.md` Fase 5 punto 3 / Fase 6 punto 3 para reflejar la corrección
  de la sección 3 (no es obligatorio para que el código funcione, pero
  reduce el riesgo de rework documentado en "Risks").
- No hay cambios de esquema SQL en este ADR — `JobAnalysisModel` y
  `ApplicationModel` ya están migrados con la forma exacta que ambos
  repositorios nuevos necesitan.
