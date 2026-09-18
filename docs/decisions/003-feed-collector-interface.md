# ADR-003: `FeedCollector` interface — location, signature, and return type

## Status

Accepted.

## Problem

`ROADMAP.md` Fase 2 asigna a `linkedin-agent` la implementación de
`app/infrastructure/linkedin/linkedin_feed_collector.py`, que "implementa
la interfaz `FeedCollector` (definida en domain/application)", pero no fija:

1. En qué carpeta exacta y con qué firma vive ese `Protocol`.
2. Qué tipo de dato devuelve `collect()` — una entidad `Job` ya construida
   (`Job.create(...)`), o un DTO intermedio que otra capa traduce.
3. Si la whitelist de URLs (`linkedin.com/feed/*`) es parte del contrato o
   un detalle de implementación.
4. Quién construye la excepción de "navegación fuera de whitelist".
5. Si el texto de `ROADMAP.md` Fase 2 punto 5 — *"`linkedin_feed_collector.py`
   ... calcula `content_hash` ... y delega el guardado al `JobRepository`
   (upsert...). Guarda cada post con `status=SCRAPED`"* — significa que
   `linkedin_feed_collector.py` importa y llama `JobRepository` directamente.

**Hallazgo adicional (detectado en revisión de `code-reviewer` sobre este mismo
ADR, no en el pedido original):** `ENGINEERING_STANDARDS.md` §4 ("Domain
Layer") y §14 ("LinkedIn Boundary"), `.claude/agents/05_linkedin.md` (sección
"Interfaz esperada") y `.claude/agents/03_domain.md` todavía muestran una
firma anterior y distinta: `class FeedCollector(Protocol): def collect(self)
-> list[JobPost]: ...`, con `JobPost` listada como entidad de dominio propia,
separada de `Job`. `JobPost` no existe en el código (`app/domain/entities/`
solo tiene `job.py`, `job_analysis.py`, `application.py`) — quedó obsoleta
cuando Fase 1 (ya cerrada) consolidó todo en la entidad única `Job`, pero
ningún ADR previo documentó esa consolidación, dejando el texto viejo vigente
en cuatro lugares, dos de los cuales (`ENGINEERING_STANDARDS.md`,
`05_linkedin.md`) son referencia de primera mano explícita para
`linkedin-agent`.

`linkedin-agent` tiene ownership exclusivo de `app/infrastructure/linkedin/**`
y su propia instrucción explícita de no implementar fuera de ese ownership
sin documentarlo — no puede resolver esta ambigüedad por su cuenta, porque
cualquiera de las opciones anteriores implica decisiones que tocan
`app/application/**` (ownership de `backend-engineer`) y el dependency rule
en general (ownership del `architect`).

Este ADR aterriza, para el caso concreto de `FeedCollector`, el criterio ya
fijado en ADR-001: los Protocols de infraestructura externa que consumen los
use cases de `backend-engineer` van en `app/application/interfaces/`
(`LLMProvider`, `FeedCollector`, `EmailDraftRepository` ya nombrados
explícitamente ahí). No reabre esa decisión general.

## Current approach

Fase 1 ya cerrada y no se modifica en este ADR:

- `app/domain/entities/job.py`: entidad `Job` con factorías `Job.create(...)`
  (siempre arranca en `JobStatus.SCRAPED`, valida no-vacíos, **no** calcula
  `content_hash`, solo exige que no esté vacío — el docstring ya anticipa
  explícitamente que quien lo calcula es "quien scrapea") y
  `Job.reconstruct(...)` (rehidratación desde persistencia).
- `app/domain/repositories/job_repository.py`: `JobRepository` (`Protocol`
  síncrono) con `save`, `get_by_id`, `get_by_content_hash` — este último ya
  pensado explícitamente para que "el collector (Fase 2)" dedupe antes de
  insertar.
- `app/application/interfaces/` y `app/application/dto/` existen vacíos
  (Fase 0, ADR-001), sin ningún `Protocol`/DTO todavío.
- `.claude/agents/05_linkedin.md` ya fija una firma tentativa, explícitamente
  marcada "a validar/precisar": `class FeedCollector(Protocol): def
  collect(self) -> list[JobPost]: ...`, y ya afirma la restricción de scope
  clave: *"Su única responsabilidad es obtener publicaciones y devolver
  datos estructurados; el resto del pipeline vive en otros agentes/capas."*

## Proposed approach

### 1. Ubicación y firma de `FeedCollector`

`app/application/interfaces/feed_collector.py` (ownership: `backend-engineer`,
igual que el resto de `application/interfaces/` según ADR-001):

```python
class FeedCollector(Protocol):
    def collect(self) -> list[RawFeedPost]: ...
```

Ya creado como skeleton (Protocol vacío, sin lógica) en este ADR, siguiendo
el mismo criterio que ADR-001 usó para crear el árbol de carpetas de Fase 0
— documentar la firma exacta es responsabilidad de `architect`; implementar
`LinkedInFeedCollector` (la clase concreta) sigue siendo 100% de
`linkedin-agent`.

Sin parámetros: el "dónde" (`linkedin.com/feed/`) y el "cuánto" (scroll
controlado, cuántos posts, rate limiting) son detalles de implementación de
`linkedin-agent`, no parte del contrato — mantiene el Protocol estable
aunque cambie la estrategia de scroll/paginación interna.

### 2. Tipo de retorno de `collect()`: DTO, no `Job`

**Decisión: `collect()` devuelve `list[RawFeedPost]`, un DTO nuevo en
`app/application/dto/raw_feed_post.py` — no `list[Job]`.**

`RawFeedPost` es un dataclass congelado, plain data carrier, sin validación
de invariantes de negocio, con los campos que `ROADMAP.md` Fase 2 ya fija
para `feed.py`/`linkedin_feed_collector.py`: `author`, `content` (ya
normalizado por `parser.py`), `content_hash` (`sha256` del `content`
normalizado, calculado en infraestructura), `url`, `published_at
(datetime | None)`. Deliberadamente **sin** `email`: `feed.py` (ROADMAP
Fase 2 punto 3) solo extrae autor/texto/timestamp/URL; la extracción de
email es del Job Analyzer LLM en Fase 3 (`CLAUDE.md`, pipeline). El use case
pasa `email=None` a `Job.create(...)` para todo `Job` recién scrapeado.

Es `CollectFeedPosts` (`backend-engineer`,
`app/application/use_cases/collect_feed_posts.py`, no creado en este ADR —
ver "Migration impact") quien traduce cada `RawFeedPost` a un `Job` vía
`Job.create(source="linkedin", author=raw.author, content=raw.content,
content_hash=raw.content_hash, email=None, url=raw.url,
published_at=raw.published_at, scraped_at=..., created_at=...)`.

**Justificación (dependency rule + ownership, no solo estilo):**

- El dependency rule (`Infrastructure → Application interfaces → Domain`)
  permite técnicamente que `linkedin-agent` importe `Job`/`EmailAddress`
  desde `app/domain/**` — el sentido de la flecha lo autoriza (igual que
  `SQLAlchemyJobRepository` importa `Job`). El problema **no** es una
  violación de dependencia; es de **ownership y acoplamiento accidental**:
  - `05_linkedin.md` ya declara explícitamente el límite de responsabilidad:
    *"Su única responsabilidad es obtener publicaciones y devolver datos
    estructurados"* — construir un `Job` implica conocer reglas de negocio
    del dominio (qué campos son obligatorios, cómo se envuelve `email` en
    `EmailAddress`, qué excepción de dominio (`InvalidDomainValueError`,
    `InvalidEmailAddressError`) manejar si un post viene con datos sucios).
    Eso es lógica de traducción a dominio, no de scraping.
  - Si `Job.create(...)` cambia de firma en una fase futura (p. ej.
    `domain-engineer` agrega un campo obligatorio nuevo), con `list[Job]`
    ese cambio se propagaría directamente a código dentro del ownership de
    `linkedin-agent` (`app/infrastructure/linkedin/**`), obligándolo a
    tocar su capa por un cambio de dominio que no inició ni controla. Con
    `list[RawFeedPost]`, ese cambio queda contenido en
    `CollectFeedPosts` (`backend-engineer`, que ya coordina con
    `domain-engineer` cuando el dominio cambia) — `linkedin-agent` no se
    entera.
  - `RawFeedPost` no valida invariantes (a propósito): la única fuente de
    verdad para "un post con autor vacío es inválido" sigue siendo
    `Job.create()`. Si `collect()` devolviera `Job` directamente,
    `linkedin-agent` tendría que decidir qué hacer ante
    `InvalidDomainValueError`/`InvalidEmailAddressError` lanzadas por el
    propio dominio dentro de su capa de infraestructura — mezclando manejo
    de errores de negocio con manejo de errores de scraping (sesión
    expirada, DOM cambiado, etc.), que sí son legítimamente suyos.
  - Consistente con el criterio general ya fijado en ADR-001 para
    `application/interfaces/`: esa carpeta aloja contratos hacia sistemas
    externos consumidos por use cases de `backend-engineer`; el propio
    `backend-engineer` es quien decide cuándo y cómo aparece una entidad de
    dominio, no la infraestructura que alimenta el contrato.
- Un DTO intermedio es exactamente el patrón que ya usa el resto de la
  arquitectura documentada (`ENGINEERING_STANDARDS.md`/ADR-001: DTOs en
  `application/dto/` para no exponer entidades directamente a través de un
  boundary) — aquí el boundary es infraestructura→aplicación en vez de
  aplicación→presentación, pero el motivo (aislar a quien no posee la
  entidad de su construcción) es el mismo.

**Corrección explícita a `ROADMAP.md` Fase 2 punto 5:** el texto actual dice
que `linkedin_feed_collector.py` "calcula `content_hash`... y delega el
guardado al `JobRepository` (upsert...)". Con esta decisión,
`linkedin_feed_collector.py` **sí** calcula `content_hash` (detalle de
infraestructura, consistente con el docstring de `Job.create`), pero **no**
llama a `JobRepository` ni decide el upsert — eso se mueve íntegramente a
`CollectFeedPosts`. Se documenta como corrección explícita (no se reescribe
`ROADMAP.md` en este ADR — el `architect` no tiene ownership de ese archivo;
`orchestrator` decide si actualiza el texto del roadmap para que coincida)
porque, tal como está redactado, contradice el punto 4 de este mismo ADR y
la restricción de scope que el propio `05_linkedin.md` ya se autoimpone.

### 3. Whitelist de URLs: detalle de implementación, no parte del contrato

La whitelist (`linkedin.com/feed/*`) **no** es parte de `FeedCollector` — no
aparece en la firma del Protocol ni en `RawFeedPost`. Es responsabilidad
exclusiva de `linkedin-agent`, dentro de `app/infrastructure/linkedin/feed.py`
(o `browser.py`), verificada programáticamente antes de cualquier navegación.

Nota explícita (no negociable, ya está en `05_linkedin.md` pero se ratifica
aquí para que quede en el ADR): si Playwright intenta navegar fuera de esa
whitelist, debe **abortar con una excepción específica** (no una genérica
`Exception`, no silenciarlo con un `try/except` vacío ni con un `log +
continue`). `linkedin-agent` decide el nombre exacto de esa excepción dentro
de su propio ownership (p. ej. algo como `DisallowedNavigationError`, sin que
este ADR fije el nombre) — no necesita heredar de `DomainError`
(`app/domain/exceptions/domain_error.py`), porque esa jerarquía está
reservada a violaciones de invariantes de *negocio*, y esto es una guardia de
*infraestructura* (una violación del scope permitido de scraping, no una
regla del dominio `Job`). `FeedCollector.collect()` puede dejarla propagar
sin declararla por nombre en el Protocol — igual que cualquier otra
excepción de infraestructura (sesión expirada, timeout de red).

### 4. Ownership de `CollectFeedPosts`

Se confirma sin cambios: `CollectFeedPosts` vive en
`app/application/use_cases/collect_feed_posts.py`, ownership de
`backend-engineer` (`.claude/agents/04_backend.md`, que ya lo nombra
explícitamente en su lista de use cases). Orquesta `FeedCollector.collect()`
+ `JobRepository` (dedup vía `get_by_content_hash` + `save`), siguiendo el
patrón de DI ya documentado en `04_backend.md` (dependencias inyectadas por
constructor, nunca instanciadas dentro del use case):

```python
class CollectFeedPosts:
    def __init__(self, feed_collector: FeedCollector, job_repository: JobRepository):
        self._feed_collector = feed_collector
        self._job_repository = job_repository
```

Esta firma es orientativa para `backend-engineer` (no se crea el archivo en
este ADR — es lógica de aplicación, fuera del ownership de `architect`).

Se confirma explícitamente: **`linkedin-agent` nunca importa `JobRepository`
ni ningún módulo de `app/infrastructure/database/**`, ni toca la base de
datos directamente.** Esto ya se deriva de su ownership estricto
(`app/infrastructure/linkedin/**` únicamente) y de la decisión del punto 2 —
al no construir `Job` ni conocer `JobRepository`, no tiene forma de
persistir nada por su cuenta, lo cual es la garantía estructural (no solo
una regla escrita) de que "su única responsabilidad es obtener publicaciones
y devolver datos estructurados".

### 5. `RawFeedPost`

Ya creado como skeleton en `app/application/dto/raw_feed_post.py` (dataclass
congelado, campos documentados en la sección 2). Ownership: `backend-engineer`
(vive en `application/dto/`, igual que el resto de DTOs por ADR-001/skeleton
doc), aunque en la práctica es `linkedin-agent` quien instancia objetos de
este tipo dentro de `linkedin_feed_collector.py` — igual que
`SQLAlchemyJobRepository` (ownership `database-agent`) instancia `Job`
(ownership `domain-engineer`) sin poseer esa clase. Poseer un tipo e
instanciarlo son cosas distintas; lo que importa para el dependency rule es
la dirección del import (`infrastructure` → `application`), que aquí es
correcta.

## Alternatives

### A. `collect()` devuelve `list[Job]` directamente

- **Trade-off:** un paso menos de traducción; aprovecha que `Job.create()`
  ya centraliza la validación, y el docstring de `Job.create` ya anticipa
  que `linkedin_feed_collector.py` es "quien scrapea" y calcula el hash.
- **Rechazada** por las razones de ownership/acoplamiento del punto 2: obliga
  a `linkedin-agent` a conocer la construcción de `Job` (incluyendo
  `EmailAddress`, excepciones de dominio, y cualquier campo nuevo que
  `domain-engineer` agregue en el futuro), rompiendo el límite de
  responsabilidad que `05_linkedin.md` ya se autoimpone y acoplando dos
  agentes con ownership distinto a través de una firma de constructor de
  entidad en vez de un contrato estable.

### B. Un `Protocol` con dos métodos — `collect_raw()` (infra) y `to_job()` (traducción, en el propio Protocol o en una función libre en `application/`)

- **Trade-off:** explicita la traducción como parte nombrada del contrato en
  vez de dejarla implícita dentro de `CollectFeedPosts`.
- **Rechazada** por sobre-especificar el Protocol: la traducción DTO→entidad
  es lógica de orquestación de un use case, no parte del "puerto hacia
  LinkedIn" en sí. Agregar un segundo método al Protocol solo para alojar
  esa traducción viola YAGNI/simplicidad (`ENGINEERING_STANDARDS.md` §31) sin
  beneficio real sobre tenerla como una función/método privado dentro de
  `CollectFeedPosts`.

### C. `RawFeedPost` como modelo Pydantic en vez de `dataclass`

- **Trade-off:** Pydantic daría validación de tipos/parseo automático y
  serialización gratis si en algún momento este DTO cruzara un boundary
  HTTP.
- **Rechazada** para este caso: `ENGINEERING_STANDARDS.md` pide Pydantic en
  los *boundaries* externos (`app/presentation/**`, DTOs de request/response
  HTTP). `RawFeedPost` es un contrato interno entre dos piezas ya dentro del
  backend (infraestructura → aplicación), no cruza serialización HTTP/JSON.
  Usar `dataclass` simple evita una dependencia y una capa de validación
  redundante con la que ya hace `Job.create()` — puede reevaluarse si en el
  futuro `RawFeedPost` (u otro DTO) necesita serializarse fuera del proceso.

## Trade-offs

| | DTO (`RawFeedPost`, elegido) | `Job` directo |
|---|---|---|
| Acoplamiento `linkedin-agent` ↔ `domain-engineer` | Bajo (solo vía `application/dto/`) | Alto (conoce constructor y excepciones de `Job`) |
| Pasos de traducción | Uno extra en `CollectFeedPosts` | Cero |
| Estabilidad del contrato ante cambios de `Job` | Alta (solo cambia el use case) | Baja (cambia también infraestructura) |
| Alineado con "solo devuelve datos estructurados" de `05_linkedin.md` | Sí, literal | No — implica lógica de dominio en infra |
| Riesgo de manejar excepciones de dominio dentro de infraestructura | Ninguno | Real (`InvalidDomainValueError`, etc.) |

## Risks

- **Riesgo bajo:** un paso de traducción adicional en `CollectFeedPosts`
  significa que, si `backend-engineer` olvida mapear un campo de
  `RawFeedPost` a `Job.create(...)`, el error aparece en tiempo de
  ejecución/test de `CollectFeedPosts`, no en tiempo de import. Mitigación:
  `testing-agent` debe cubrir `CollectFeedPosts` con un
  `FakeFeedCollector` (devolviendo `RawFeedPost` fijos) + `InMemoryJobRepository`,
  verificando el mapeo campo a campo — es una instrucción natural para la
  Fase 2, no requiere tooling nuevo.
- **Riesgo bajo:** este ADR corrige (no reescribe) `ROADMAP.md` Fase 2 punto
  5 en su interpretación de "quién llama a `JobRepository`". Si
  `linkedin-agent` lee el ROADMAP de forma literal sin conocer este ADR,
  podría intentar importar `JobRepository` directamente. Mitigación: este
  ADR es la referencia que `orchestrator` debe entregar a `linkedin-agent`
  antes de que empiece Fase 2 (tal como se solicitó explícitamente en esta
  tarea).
- **Riesgo medio-alto (deuda técnica documental, `DEBT-ADR003-01`,
  severidad MEDIA-ALTA, detectada por `code-reviewer`):**
  `ENGINEERING_STANDARDS.md` §4/§14, `.claude/agents/05_linkedin.md` y
  `.claude/agents/03_domain.md` siguen mostrando la firma obsoleta
  `FeedCollector.collect() -> list[JobPost]` (ver "Problem" más arriba). Como
  `05_linkedin.md` y `ENGINEERING_STANDARDS.md` son referencia directa citada
  por la propia ficha de `linkedin-agent`, existe riesgo real de que
  `linkedin-agent` implemente contra la firma vieja si no se le entrega este
  ADR explícitamente antes de empezar. **Impacto:** confusión/rework al
  implementar Fase 2; ningún riesgo de romper el dependency rule (ambas
  firmas son Protocols puros), pero sí de inconsistencia entre documentos
  normativos y el código real. **Solución recomendada:** `orchestrator`
  actualiza el snippet inline en esos cuatro lugares para reflejar
  `list[RawFeedPost]` (sin `JobPost`), referenciando este ADR. **Por qué no
  se corrige en este ADR:** `ENGINEERING_STANDARDS.md` y `.claude/agents/*.md`
  no son ownership de `architect` (`docs/decisions/**`/`docs/architecture/**`
  únicamente, ver `docs/agents/AGENTS.md`) — corregirlos aquí violaría el
  límite de ownership que este mismo documento exige respetar en otros
  agentes.
- **Riesgo nulo de violar el dependency rule por esta decisión en sí** — el
  DTO vive en `application/dto/`, el Protocol en `application/interfaces/`,
  ambos consumidos/implementados en la dirección correcta
  (`Infrastructure → Application interfaces → Domain`).

## Migration impact

- Los dos archivos de skeleton (`app/application/interfaces/feed_collector.py`,
  `app/application/dto/raw_feed_post.py`) ya existen, vacíos de lógica de
  negocio, listos para que `linkedin-agent` los implemente/consuma sin
  ambigüedad.
- `backend-engineer` debe crear `app/application/use_cases/collect_feed_posts.py`
  en Fase 2 (no creado por este ADR) siguiendo la firma orientativa de la
  sección 4, y el endpoint `POST /scrape` en
  `app/presentation/api/routes/` que lo invoca (ya asignado a
  `backend-engineer` por `ROADMAP.md` Fase 2 punto 6, sin cambios).
- `linkedin-agent` debe implementar `LinkedInFeedCollector` en
  `app/infrastructure/linkedin/linkedin_feed_collector.py` devolviendo
  `list[RawFeedPost]` (importando el DTO desde `app/application/dto/`, el
  Protocol desde `app/application/interfaces/` solo para el `-> None`
  implícito de structural typing — no necesita heredar de nada), sin
  importar `JobRepository` ni `Job`.
- Si en una fase posterior aparece un segundo collector (por ejemplo, otra
  fuente además de LinkedIn), `FeedCollector`/`RawFeedPost` ya están
  diseñados para eso: cualquier implementación nueva satisface el mismo
  Protocol y devuelve el mismo DTO, sin tocar `CollectFeedPosts`.
