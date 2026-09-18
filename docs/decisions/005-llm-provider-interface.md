# ADR-005: `LLMProvider` interface — location, DTOs, and infrastructure exceptions

## Status

Accepted.

## Problem

`ROADMAP.md` Fase 3 asigna a `llm-agent` implementar `analyze_job` sobre
una interfaz `LLMProvider` "(definida en domain/application)"; Fase 5
amplía la misma interfaz con `generate_email`. `docs/agents/AGENTS.md` §10
ya da un boceto orientativo, explícitamente marcado como "Example":

```python
class LLMProvider(Protocol):
    def analyze_job(self, content: str) -> JobAnalysis: ...
    def generate_email(self, context: EmailContext) -> GeneratedEmail: ...
```

Ese boceto deja sin resolver, y bloquea a `llm-agent` antes de empezar
Fase 3:

1. **Ubicación exacta** del Protocol (`domain/` vs `application/`).
2. **Tipo de retorno de `analyze_job`**: el boceto devuelve `JobAnalysis`
   (la entidad de dominio) directamente — mezclando el resultado crudo de
   un proveedor externo no confiable con una entidad que tiene invariantes
   de negocio propias (`JobAnalysis.__init__` ya valida `job_type` no
   vacío, `match_score` en `[0,1]`, y la regla "no `generated_email` sin
   `recommended_cv`"). Esto es exactamente el mismo problema que ADR-003
   ya resolvió para `FeedCollector`/`RawFeedPost`, sin resolverlo todavía
   aquí.
3. **Firma exacta de `generate_email`**: qué recibe como input. TOKEN_OPTIMIZATION.md
   §7 exige explícitamente *"no mandar el PDF completo del CV... mandar un
   resumen corto ya redactado"* — el boceto de AGENTS.md ya usa un
   `context: EmailContext` pero sin fijar sus campos, dejando abierto si
   `cv-matching-agent` debe pasar una ruta a PDF (violando TOKEN_OPTIMIZATION.md)
   o un string.
4. **Excepciones de infraestructura**: qué lanza `LLMProvider` cuando la
   respuesta del proveedor no valida contra el schema Pydantic esperado, o
   cuando la llamada falla (rate limit, timeout, red) — sin nombre ni
   ubicación fijados, `llm-agent` podría inventar sus propias excepciones
   ad-hoc, o peor, dejar propagar excepciones crudas del SDK de OpenAI/
   Anthropic hacia `application/`, violando el dependency rule.

`app/infrastructure/linkedin/exceptions.py` (Fase 2, ya implementado) ya
fija el patrón de excepciones de infraestructura para este proyecto (raíz +
subclases, sin heredar de `DomainError`) — este ADR aplica el mismo
criterio al dominio LLM en vez de inventar uno nuevo.

## Current approach

- `app/application/interfaces/feed_collector.py` y
  `app/application/dto/raw_feed_post.py` (ADR-003) ya establecen el patrón
  de referencia completo: Protocol en `application/interfaces/`, DTO plano
  (dataclass congelado, sin invariantes de negocio) en `application/dto/`,
  para que la infraestructura externa nunca tenga que construir una
  entidad de dominio válida ni conocer sus reglas.
- `app/domain/entities/job_analysis.py`: `JobAnalysis` ya implementada
  (Fase 1), con invariantes propias (ver punto 2 de "Problem").
- No existe `app/application/interfaces/llm_provider.py` ni ningún DTO de
  LLM en `app/application/dto/`.
- No existe `app/infrastructure/llm/` con contenido (carpeta vacía del
  skeleton de Fase 0).
- `app/infrastructure/linkedin/exceptions.py`: patrón ya usado para
  excepciones de infraestructura (raíz `LinkedInInfrastructureError` +
  subclases concretas, deliberadamente sin heredar de
  `app.domain.exceptions.domain_error.DomainError`).

## Proposed approach

### 1. Ubicación: `app/application/interfaces/llm_provider.py`

Mismo criterio que ADR-003 fijó para `FeedCollector` y que ADR-001 ya
definió en general: Protocols hacia sistemas externos consumidos por use
cases de `backend-engineer`/`llm-agent` (no persistencia de una entidad
propia) van en `application/interfaces/`, no en `domain/`. `LLMProvider`
ya está nombrado explícitamente ahí desde ADR-001 (*"`LLMProvider`,
`FeedCollector`, `EmailDraftRepository` ya nombrados explícitamente"*) —
este ADR no reabre esa ubicación, solo fija la firma completa.

Ownership del archivo: `backend-engineer` (dueño de `application/**`,
igual que `feed_collector.py`), consumido/implementado por `llm-agent`
dentro de `app/infrastructure/llm/`.

### 2. `analyze_job`: devuelve un DTO (`JobAnalysisResult`), no `JobAnalysis`

```python
class LLMProvider(Protocol):
    def analyze_job(self, content: str) -> JobAnalysisResult: ...
```

**Decisión: sí, un DTO propio — `JobAnalysisResult`, en
`app/application/dto/job_analysis_result.py`** (dataclass congelado, sin
`__post_init__` con invariantes de negocio, siguiendo al pie de la letra el
criterio ya usado por `RawFeedPost`).

```python
@dataclass(frozen=True, slots=True)
class JobAnalysisResult:
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
```

Mapea 1:1 el JSON estructurado que `ROADMAP.md` Fase 3 punto 3 ya fija como
salida esperada del proveedor. `AnalyzeJobPost`
(`app/application/use_cases/analyze_job_post.py`, no creado en este ADR —
ownership: `backend-engineer`/`llm-agent`, coordinar, según `ROADMAP.md`
Fase 3 punto 4) es quien traduce este DTO a dominio:

- Si `is_job` es `False`: **no construye `JobAnalysis`** (no tiene sentido
  con `job_type` vacío/irrelevante) — solo llama `job.mark_analyzed()`
  seguido de `job.mark_not_relevant()`.
- Si `is_job` es `True`: construye `JobAnalysis(job_id=job.id,
  job_type=result.job_type, seniority=result.seniority,
  skills=result.skills, languages=result.languages,
  frameworks=result.frameworks, cloud=result.cloud,
  ai_related=result.ai_related)`, persiste con
  `JobAnalysisRepository.save` (ADR-004), y llama
  `job.mark_analyzed()` seguido de `job.mark_relevant()`.

Razonamiento (mismo que ADR-003, aplicado a este boundary):

- **Ownership/acoplamiento.** Si `analyze_job` devolviera `JobAnalysis`
  directamente, `llm-agent` (`app/infrastructure/llm/**`) tendría que
  construir una entidad de dominio válida — conociendo sus invariantes
  (`match_score` en `[0,1]`, "no `generated_email` sin `recommended_cv`") y
  manejando `InvalidDomainValueError` si el proveedor devuelve algo
  inconsistente, mezclando manejo de errores de negocio con manejo de
  errores de proveedor LLM (rate limit, timeout, JSON malformado) que sí
  son legítimamente suyos. Un DTO plano aísla completamente esa
  responsabilidad en `AnalyzeJobPost`.
- **Estabilidad del contrato.** Si `domain-engineer` agrega un campo
  obligatorio nuevo a `JobAnalysis` en una fase futura, ese cambio se
  contiene en `AnalyzeJobPost` — `llm-agent` no se entera ni tiene que
  tocar `app/infrastructure/llm/**`.
- **`job_type`, no `job_category`.** El JSON de salida documentado en
  `ROADMAP.md` usa la clave `job_category`; el DTO usa `job_type` para
  hablar el mismo vocabulario que la entidad `JobAnalysis` (que ya tiene un
  campo `job_type`, no `job_category` — ver `app/domain/entities/job_analysis.py`).
  La traducción de la clave JSON del proveedor (`job_category`) al campo
  del DTO (`job_type`) ocurre dentro de `OpenAIProvider`/`AnthropicProvider`
  (ownership `llm-agent`, en su propio modelo Pydantic interno de
  validación de la respuesta cruda) — `analyze_job()` ya devuelve algo que
  habla el vocabulario del dominio, no el de la respuesta JSON cruda del
  proveedor. Esto es deliberado para que solo exista una fuente de verdad
  del nombre del concepto ("tipo de trabajo") en todo el proyecto
  (`AGENTS.md` §20).
- **Validación de la lista cerrada de categorías** (Java, Python, AI/ML,
  Deep Learning, JavaScript/Node, Go, Elixir, Full Stack, Other — ver
  `ROADMAP.md` Fase 3 punto 2) ocurre en el modelo Pydantic interno del
  proveedor concreto (p. ej. `class RawAnalysisResponse(BaseModel):
  job_category: Literal[...]`, ownership `llm-agent`), **no** en
  `JobAnalysisResult`: el DTO de aplicación es deliberadamente tan laxo
  como la propia entidad `JobAnalysis` (que solo exige `job_type` no
  vacío) — no duplica una validación más estricta que la del dominio en una
  capa intermedia.
- **`confidence: float` no tiene columna en `JobAnalysis`/`job_analysis`.**
  Es un campo transitorio: `AnalyzeJobPost` lo usa únicamente para decidir
  el ruteo `is_job`/confianza mínima antes de marcar `RELEVANT` (p. ej. si
  en el futuro se agrega un umbral de confianza), pero no se persiste en
  ningún lugar hoy. Si más adelante se quiere loguear/analizar confidence
  histórico (útil para `token-optimization-agent`, ver
  `TOKEN_OPTIMIZATION.md` §10), eso requiere que `domain-engineer` agregue
  una columna nueva — fuera del alcance de este ADR, se documenta como
  posibilidad futura, no como decisión tomada.
- **`email_addresses: tuple[str, ...]` (plano, plural) — hallazgo de deuda
  técnica cross-agente, no resuelto por este ADR:** `CLAUDE.md` (pipeline)
  y `ROADMAP.md` Fase 3 esperan explícitamente que el Analyzer extraiga
  el email de contacto del post. Sin embargo, **`Job` (la entidad, ya
  implementada) no tiene ningún método para actualizar `email` después de
  `Job.create(...)`** — su único setter implícito es el constructor;
  `Job` solo expone `mark_*` para transiciones de `status`, ninguno para
  mutar `email`. Esto significa que, tal como está implementado hoy el
  dominio, `AnalyzeJobPost` **no tiene forma de persistir el email
  detectado en Fase 3** sin que `domain-engineer` agregue un método nuevo
  (p. ej. `Job.record_extracted_email(email: EmailAddress) -> None`, sin
  transición de estado asociada, análogo en espíritu a
  `JobAnalysis.record_cv_recommendation`). **Este ADR no lo agrega**
  (fuera de mi ownership, y `domain-engineer` ya declaró estas entidades
  "estables e implementadas" para esta tarea) — se reporta explícitamente
  como deuda técnica en "Risks" para que `orchestrator` lo enrute a
  `domain-engineer` antes de que Fase 3 se dé por completa. El DTO
  `JobAnalysisResult.email_addresses` queda definido igual (refleja lo que
  el proveedor LLM puede extraer), independientemente de si hoy existe un
  lugar del dominio donde guardarlo.

### 3. `generate_email`: recibe un DTO (`EmailContext`) con `cv_summary: str`, devuelve un DTO (`GeneratedEmail`)

```python
class LLMProvider(Protocol):
    def generate_email(self, context: EmailContext) -> GeneratedEmail: ...
```

```python
@dataclass(frozen=True, slots=True)
class EmailContext:
    job_type: str
    seniority: str | None
    skills: tuple[str, ...]
    languages: tuple[str, ...]
    frameworks: tuple[str, ...]
    author: str
    job_content: str
    cv_summary: str
```

```python
@dataclass(frozen=True, slots=True)
class GeneratedEmail:
    subject: str
    body: str
```

Ambos DTOs nuevos en `app/application/dto/` (`email_context.py`,
`generated_email.py`), mismo criterio que `JobAnalysisResult`/`RawFeedPost`.
Se mantiene el nombre `EmailContext`/`GeneratedEmail` ya sugerido por
`docs/agents/AGENTS.md` §10 (evita drift documental innecesario entre el
boceto ya publicado y esta decisión), pero fijando ahora sus campos, que
antes no estaban definidos.

Razonamiento de los campos de `EmailContext`:

- **`cv_summary: str`, no un `cv_path`/ruta a PDF.** Es el requisito
  explícito de `TOKEN_OPTIMIZATION.md` §7 (*"No mandar el PDF completo del
  CV... mandar un resumen corto ya redactado (3-5 líneas)... El resumen se
  escribe una sola vez por CV, no se regenera por cada oferta"*) y de
  `ROADMAP.md` Fase 5 (*"usa el resumen de CV que provee
  `cv-matching-agent`, no el PDF completo"*). Quien produce ese string es
  `cv-matching-agent` (Fase 4, desde `config/cvs.yaml` o el catálogo que
  defina) — `GenerateApplicationEmail` (Fase 5, `llm-agent`/
  `backend-engineer`) lo obtiene leyendo `JobAnalysis.recommended_cv`
  (nombre/id del CV ya elegido) y resolviendo el resumen correspondiente
  contra el catálogo de `cv-matching-agent`; el mecanismo exacto de esa
  resolución (¿`CVCatalogRepository` expone un método
  `get_summary(cv_id) -> str`? ¿el resumen vive en `cvs.yaml` junto a
  `skills`?) es decisión de `cv-matching-agent` dentro de su propio
  ownership (`application/cv/`, `ROADMAP.md` Fase 4) — este ADR solo fija
  que, sea cual sea el mecanismo, lo que cruza hacia `LLMProvider` es un
  `str` ya resuelto, nunca una ruta de archivo.
- **`job_content: str` (el post original, no solo campos estructurados) —
  resuelve una brecha real sin tocar el dominio.** `JobAnalysis` no tiene
  campos `company`/`role_title` separados (el modelo de datos de
  `CLAUDE.md` y la entidad ya implementada solo tienen `job_type` como
  categoría cerrada, no el nombre literal del puesto ni de la empresa).
  Sin esos campos estructurados, el Email Generator no podría redactar
  "Estimados, postulo a la posición de X en Y..." de forma concreta.
  Pasar el `content` original del `Job` (posiblemente truncado, ver
  `TOKEN_OPTIMIZATION.md` §3) como parte del contexto permite que el LLM
  redacte con datos reales tomados del propio post (coherente con la
  instrucción de `ROADMAP.md` Fase 5 punto 2: *"no inventar
  experiencia/tecnologías/empresas que no estén"* — aquí, no inventar
  tampoco la empresa/puesto, citando el texto real). **Se documenta
  explícitamente como hallazgo, no como decisión definitiva:** si
  `domain-engineer`/`llm-agent` prefieren en el futuro que el Analyzer
  extraiga `company`/`role_title` como campos estructurados propios (lo
  cual requeriría ampliar `JobAnalysis`, fuera de este ADR), `EmailContext`
  podría simplificarse reemplazando `job_content` por esos campos — no es
  necesario para que Fase 5 funcione hoy.
- **`author: str`** (de `Job.author`): señal adicional de bajo costo (ya
  existe, no requiere cambios de dominio) — quién publicó el post, útil
  como respaldo si `job_content` no menciona explícitamente a quién
  dirigirse.
- **No se incluye `url` ni `published_at`**: no aportan valor a la
  redacción de un email de postulación: mantener el DTO mínimo
  (`ENGINEERING_STANDARDS.md` §31, YAGNI) en vez de pasar todo lo que
  `Job` expone "por si acaso".
- **Truncado/limpieza de `job_content` es responsabilidad del caller**
  (`GenerateApplicationEmail`, coordinado con `token-optimization-agent`
  según `TOKEN_OPTIMIZATION.md` §3), no de este Protocol — mismo criterio
  que ya aplica a `analyze_job(content: str)`.

`GenerateApplicationEmail` (`app/application/use_cases/generate_application_email.py`,
no creado en este ADR) traduce `GeneratedEmail` a dominio llamando
`job_analysis.record_generated_email(subject=result.subject,
body=result.body)` (método ya implementado, exige `recommended_cv` previo
— consistente con que Fase 5 corre después de Fase 4), persiste vía
`JobAnalysisRepository.save` (ADR-004), y mueve `Job.status` a
`EMAIL_GENERATED`. **No** crea una `Application` (ver ADR-004, sección 3 —
corrección explícita de que `applications` recién se escribe en Fase 7).

### 4. Excepciones de infraestructura LLM

**Ubicación: `app/infrastructure/llm/exceptions.py`**, análogo exacto a
`app/infrastructure/linkedin/exceptions.py` (mismo patrón: raíz abstracta +
subclases concretas, nunca heredan de
`app.domain.exceptions.domain_error.DomainError` porque no son violaciones
de invariantes de *negocio*, son fallas de infraestructura no confiable —
un proveedor LLM externo, igual que Playwright/el DOM de LinkedIn).

```python
class LLMInfrastructureError(Exception):
    """Root of every exception raised by app.infrastructure.llm. Never
    raised directly."""


class LLMProviderError(LLMInfrastructureError):
    """The call to the provider itself failed: timeout, rate limit,
    network error, authentication failure. Wraps the underlying SDK
    exception via `raise ... from exc`; never lets the raw OpenAI/
    Anthropic SDK exception type propagate past `infrastructure/llm/`."""


class LLMResponseValidationError(LLMInfrastructureError):
    """The provider responded, but its content did not validate against
    the expected Pydantic schema (missing field, wrong type, category
    outside the closed list, malformed JSON). Wraps the underlying
    `pydantic.ValidationError` via `raise ... from exc`."""
```

Decisiones:

- **Dos subclases, no más, por ahora** — cubren exactamente los dos casos
  que la tarea pide nombrar explícitamente (fallo de la llamada en sí;
  fallo de validación de la respuesta). No se agrega, por ejemplo, una
  excepción separada para "rate limit" vs "timeout" vs "auth failure":
  `LLMProviderError` los cubre todos como una sola categoría ("la llamada
  no se completó con éxito") porque ningún use case documentado en
  `ROADMAP.md` necesita hoy reaccionar de forma distinta a cada una — YAGNI,
  mismo criterio que `LinkedInInfrastructureError` no distingue entre tipos
  de timeout de red.
- **`LLMResponseValidationError` envuelve, no reemplaza, el
  `pydantic.ValidationError` original** (`raise LLMResponseValidationError(...)
  from exc`) para no perder el detalle de qué campo falló al debuggear,
  sin dejar que el tipo de excepción de Pydantic se filtre como contrato
  público de `LLMProvider`.
- **Ninguna de las dos declara el contenido crudo de la respuesta del
  proveedor en el mensaje de excepción sin criterio** — nota de higiene
  para `llm-agent` (no una regla nueva de este ADR, ya se deriva de
  `ENGINEERING_STANDARDS.md`/seguridad general): truncar/no loguear texto
  potencialmente largo o con PII del post analizado en mensajes de
  excepción que puedan terminar en logs persistentes; `security-agent`
  revisa esto en su paso obligatorio.
- **El Protocol `LLMProvider` no declara estas excepciones por nombre en
  su firma** (no hay `raises` tipado en Python) — mismo criterio que
  `FeedCollector.collect()` en ADR-003: se documentan en el docstring del
  Protocol como parte del contrato informal, no se filtran hacia
  `application/` como tipos que los use cases deban importar
  obligatoriamente, aunque **pueden** capturarlas explícitamente si
  necesitan un manejo distinto (p. ej. reintentar ante
  `LLMProviderError` pero no ante `LLMResponseValidationError`, que no se
  arregla reintentando la misma llamada).

## Alternatives

### A. `analyze_job`/`generate_email` devuelven las entidades de dominio directamente (el boceto original de `AGENTS.md` §10)

- **Trade-off:** un paso menos de traducción en los use cases.
- **Rechazada** por las mismas razones que ADR-003 rechazó la alternativa
  equivalente para `FeedCollector`: acopla a `llm-agent` con la
  construcción y las invariantes de `JobAnalysis`, y hace que cualquier
  cambio futuro en la entidad de dominio se propague directamente a
  `app/infrastructure/llm/**`.

### B. `EmailContext` recibe la entidad `JobAnalysis` completa en vez de un DTO con campos sueltos

- **Trade-off:** menos campos que mantener sincronizados manualmente si
  `JobAnalysis` cambia; `llm-agent` leería directamente
  `job_analysis.skills`, etc. Técnicamente no viola el dependency rule
  (Infrastructure puede importar Domain).
- **Rechazada:** aunque es legal por dirección de dependencia, acopla la
  forma exacta del contexto de generación de email a la forma interna de
  `JobAnalysis` (que incluye campos irrelevantes para un email, como
  `match_score`, `recommended_cv` como string en vez del resumen ya
  resuelto) y no resuelve el problema real (`cv_summary` no es un campo de
  `JobAnalysis`, tendría que pasarse igual como parámetro aparte, quedando
  una firma híbrida entidad+parámetro suelto). Un DTO explícito con
  exactamente los campos que el proveedor necesita es más simple de
  fakear en tests (`FakeLLMProvider`) y dice, por su propia forma, todo lo
  que un email necesita — sin tener que leer `JobAnalysis` para saberlo.

### C. Una excepción única `LLMError` sin distinguir fallo de llamada vs. fallo de validación

- **Trade-off:** más simple (un solo tipo).
- **Rechazada:** la tarea pide explícitamente distinguir ambos casos, y
  son accionables de forma distinta por el caller (un fallo de llamada
  puede reintentarse con backoff; un fallo de validación de schema
  probablemente no se arregla reintentando la misma llamada — indica un
  prompt roto o un cambio no anunciado en el formato de respuesta del
  proveedor). Distinguirlas es información útil sin costo real de
  complejidad (dos clases pequeñas).

## Trade-offs

| | DTOs + excepciones tipadas (elegido) | Entidades directas / excepción única |
|---|---|---|
| Acoplamiento `llm-agent` ↔ `domain-engineer` | Bajo | Alto |
| Testabilidad (`FakeLLMProvider`) | Alta (DTOs triviales de construir) | Media (hay que construir entidades válidas) |
| Capacidad de reaccionar distinto a rate-limit vs. schema roto | Sí | No |
| Alineado con ADR-003 (mismo criterio ya validado) | Sí | No |

## Risks

- **Riesgo medio-alto, deuda técnica cross-agente real (no de este ADR en
  sí, sino descubierta al escribirlo — `DEBT-ADR005-01`):** `Job` no tiene
  forma de persistir el email extraído por el Analyzer (ver sección 2).
  **Impacto:** Fase 3 no puede cumplir literalmente el pipeline de
  `CLAUDE.md` ("Job Analyzer (LLM) → ... email") hasta que
  `domain-engineer` agregue un método a `Job` (p. ej.
  `record_extracted_email`). **Severidad:** media-alta porque bloquea un
  campo explícitamente listado en el modelo de datos de `CLAUDE.md`
  (`jobs.email`), aunque no bloquea el resto del pipeline (clasificación,
  CV matching, generación de email pueden avanzar sin este campo). **Por
  qué no se corrige en este ADR:** modificar `Job` es ownership de
  `domain-engineer`, explícitamente fuera de esta tarea ("no las
  rediseñes, ya están implementadas"). **Solución recomendada:**
  `orchestrator` asigna a `domain-engineer` agregar
  `Job.record_extracted_email(email: EmailAddress) -> None` (sin
  transición de estado) antes de o durante Fase 3, coordinando con
  `llm-agent`.
- **Riesgo medio, deuda técnica cross-agente real (`DEBT-ADR005-02`):**
  `JobAnalysis` no tiene campos estructurados `company`/`role_title`; este
  ADR usa `job_content` (el post original) como paliativo para que el
  Email Generator tenga con qué redactar un email concreto. **Impacto:**
  funciona para Fase 5, pero depende de que el LLM extraiga
  correctamente esos datos del texto libre en cada llamada de
  `generate_email`, en vez de reusar datos ya estructurados y validados
  una sola vez en Fase 3 — puede producir inconsistencias si el LLM lee el
  `job_content` distinto en Fase 3 (Analyzer) y Fase 5 (Email Generator).
  **Severidad:** media — no bloquea, pero es una fuente plausible de bugs
  de calidad de output. **Solución recomendada:** si se observa este
  problema en la práctica, `domain-engineer` evalúa agregar
  `company`/`role_title` a `JobAnalysis` en un ADR futuro. **Por qué no se
  corrige ahora:** mismo motivo que el punto anterior — fuera de ownership
  de `architect`, y no bloquea el MVP.
- **Riesgo bajo:** igual que ADR-003, si `llm-agent` empieza Fase 3 sin
  conocer este ADR, podría implementar contra el boceto crudo de
  `AGENTS.md` §10 (`analyze_job(content) -> JobAnalysis`) en vez de
  `JobAnalysisResult`. Mitigación: `orchestrator` entrega este ADR
  explícitamente antes de Fase 3.
- **Riesgo nulo de violar el dependency rule por este ADR en sí** — el
  Protocol vive en `application/interfaces/`, los DTOs en
  `application/dto/`, las excepciones en `infrastructure/llm/` (no
  heredan de `DomainError`, pero tampoco necesitan hacerlo — no son
  consumidas por `domain/`).

## Migration impact

- `llm-agent` implementa, en Fase 3: `app/infrastructure/llm/openai_provider.py`
  / `anthropic_provider.py` (al menos uno; el ROADMAP no exige ambos desde
  el inicio) con `analyze_job`, más `app/infrastructure/llm/exceptions.py`;
  en Fase 5, agrega `generate_email` a la(s) misma(s) clase(s).
- `backend-engineer` crea `app/application/interfaces/llm_provider.py`
  (Protocol completo, ambos métodos, aunque `generate_email` no se use
  hasta Fase 5 — se fija la firma completa ahora para no romper el
  Protocol entre fases) y los tres DTOs nuevos en `app/application/dto/`:
  `job_analysis_result.py`, `email_context.py`, `generated_email.py`.
- `testing-agent` agrega `FakeLLMProvider` (`tests/`, implementa
  `LLMProvider` por structural typing, devuelve `JobAnalysisResult`/
  `GeneratedEmail` fijos desde fixtures) desde Fase 3, ampliado en Fase 5 —
  nunca se llama al proveedor real en unit tests (`ROADMAP.md` Fase 3
  punto 6, Fase 5 punto 4).
- `orchestrator` debe asignar a `domain-engineer` los dos hallazgos de
  "Risks" (`DEBT-ADR005-01`, email; `DEBT-ADR005-02`, company/role_title)
  antes de considerar Fase 3/5 completamente cerradas, o documentar
  explícitamente por qué se aceptan como deuda técnica permanente si se
  decide no resolverlas.
