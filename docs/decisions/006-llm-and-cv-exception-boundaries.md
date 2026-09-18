# ADR-006: `LLMProviderError`/`LLMResponseValidationError`/`CVInfrastructureError` — moving to a neutral `application/` boundary

## Status

Accepted.

## Problem

`code-reviewer`, en una revisión de conjunto, reportó un hallazgo `MEDIUM`:
dos use cases de `backend-engineer` importan excepciones directamente desde
`app.infrastructure.*`, violando la dirección de dependencia fijada en
`ENGINEERING_STANDARDS.md` §2 y `docs/agents/AGENTS.md` §26
(`Infrastructure → Application interfaces → Domain`, nunca al revés):

```python
# app/application/use_cases/analyze_job_post.py
from app.infrastructure.llm.exceptions import LLMProviderError, LLMResponseValidationError

# app/application/use_cases/generate_application_email.py
from app.infrastructure.llm.exceptions import LLMProviderError, LLMResponseValidationError
from app.infrastructure.cv.exceptions import CVInfrastructureError
```

Es la misma categoría de violación que ya se corrigió antes moviendo
`candidate_filter.py` de `app/infrastructure/llm/` a
`app/application/services/` (ver el docstring de `analyze_job_post.py`,
punto 1) — pero, a diferencia de ese caso (una función pura sin ninguna
razón para vivir en infraestructura), acá las clases en cuestión **son**
legítimamente excepciones de infraestructura no confiable (un proveedor
LLM externo; un YAML + filesystem) — no pueden simplemente "moverse
íntegras" a `application/` sin dejar algo coherente atrás para
`llm-agent`/`cv-matching-agent`.

`ADR-005` sección 4 ya documentó el patrón de excepciones de
`app.infrastructure.llm` (raíz `LLMInfrastructureError` + subclases
concretas, nunca heredan de `DomainError`) sin resolver este punto — de
hecho, ADR-005 fija explícitamente que el `Protocol` `LLMProvider` "puede
propagar `LLMProviderError`/`LLMResponseValidationError`... este `Protocol`
no las declara por nombre en la firma... se documentan aquí como parte del
contrato informal, sin que `application/` tenga que importarlas
obligatoriamente" — pero los dos use cases reales sí las importan y las
capturan explícitamente (`except (LLMProviderError,
LLMResponseValidationError):`), porque necesitan reaccionar distinto ante
un fallo transitorio de proveedor vs. cualquier otra excepción. ADR-005 dejó
sin resolver **de dónde** deben importarse cuando eso ocurre.

**Decisión ya tomada, no se reabre en este ADR:** de las dos alternativas
evaluadas —(A) dejar que `application/` importe desde `infrastructure/`
documentándolo como excepción aceptada al dependency rule, vs. (B) mover
las excepciones a un punto neutral en `application/`, del que
`infrastructure/` herede— el usuario ya eligió **(B)**.

Este ADR fija, dentro de la opción B ya elegida: (1) qué clases exactas se
mueven y cuáles se quedan en `infrastructure/`; (2) en qué archivo/paquete
exacto de `application/` viven las que se mueven; (3) el criterio general
para decidir esto en casos futuros similares, no solo para este caso
puntual.

## Current approach

- `app/infrastructure/llm/exceptions.py`: `LLMInfrastructureError` (raíz,
  nunca se lanza directamente, **no** importada hoy por `application/`),
  `LLMProviderError` y `LLMResponseValidationError` (ambas subclases
  directas de la raíz, **sí** importadas hoy por `analyze_job_post.py` y
  `generate_application_email.py`, y también por
  `app/presentation/api/routes/jobs.py` — ver "Risks").
- `app/infrastructure/cv/exceptions.py`: `CVInfrastructureError` (raíz,
  nunca se lanza directamente, **sí** importada hoy por
  `generate_application_email.py` y también por
  `app/presentation/api/routes/jobs.py`), y cuatro subclases concretas
  (`CVCatalogNotFoundError`, `CVCatalogMalformedError`,
  `CVFileNotFoundError`, `CVNotFoundError`) que **ninguna** capa de
  `application/` importa por nombre hoy (`select_best_cv.py` solo las
  menciona en un docstring, sin importarlas).
- `app/application/interfaces/llm_provider.py`: Protocol `LLMProvider`,
  ownership `backend-engineer` (ADR-005), ya documenta en su docstring que
  ambos métodos pueden propagar `LLMProviderError`/
  `LLMResponseValidationError` sin declararlas por nombre en la firma.
- `app/application/cv/cv_catalog.py`: Protocol `CVCatalog`, ownership
  `cv-matching-agent` (`docs/architecture/clean-architecture-skeleton.md`
  nota 1, tabla de ownership), ya documenta en su docstring que
  `list_cvs`/`get_summary` pueden propagar `CVInfrastructureError`/
  `CVNotFoundError`.

## Proposed approach

### Criterio general (aplica a ambos casos y a casos futuros similares)

**Solo se mueve a `application/` la excepción que `application/` importa y
captura hoy por nombre — no toda la jerarquía.** El resto de la jerarquía
(la raíz, si no se importa; las subclases concretas específicas de
infraestructura) se queda en `infrastructure/`, ahora heredando de la
clase movida en vez de la que se queda atrás. Esto ya lo fija
explícitamente el pedido de esta tarea y se ratifica aquí como regla
general: mover solo lo que cruza el boundary hoy evita mover código sin
necesidad real (YAGNI, `ENGINEERING_STANDARDS.md` §31) y mantiene en
`infrastructure/` toda subclase que ningún use case necesita distinguir
todavía.

**El paquete de destino dentro de `application/` lo determina quién posee
el Protocol/contrato al que la excepción pertenece semánticamente — no
quién posee el use case que hoy la captura.** Es el mismo criterio que
`ADR-001` (nota 3) ya fija para la ubicación de un Protocol según ownership
compartido de consumidor+implementación, extendido aquí a las excepciones
que forman parte del contrato informal de ese Protocol (su "raises"
documentado en el docstring, ya que Python no tiene `raises` tipado — ver
ADR-003/ADR-005). Un Protocol es consumido legítimamente por use cases de
distintos agentes (igual que `JobRepository`, propiedad de
`domain-engineer`, es consumido por use cases de `backend-engineer` y
`llm-agent` sin que eso mueva `JobRepository` a sus carpetas) — que
`GenerateApplicationEmail` (ownership `backend-engineer`) sea quien hoy
captura `CVInfrastructureError` no convierte a `backend-engineer` en el
dueño de esa excepción; el dueño sigue siendo quien posee el Protocol
`CVCatalog` al que esa excepción documenta como parte de su contrato.

Aplicando ambos criterios:

### 1. `LLMProviderError`/`LLMResponseValidationError` → `app/application/interfaces/llm_provider_errors.py` (nuevo archivo)

Ambas clases se mueven tal cual (mismo nombre, mismo docstring de negocio,
sin `__init__` propio hoy) a un archivo nuevo en `application/interfaces/`,
junto al Protocol `LLMProvider` que ya documenta su contrato — ownership
`backend-engineer`, igual que `llm_provider.py` (ADR-005).

**Archivo nuevo, no el mismo `llm_provider.py`:** son dos clases con
docstrings sustanciales (no una utilidad trivial de una línea), y mezclar
la definición del `Protocol` con la jerarquía de excepciones que lo
acompaña reduce cohesión sin necesidad — el mismo criterio que ya separa
`infrastructure/llm/exceptions.py` de cualquier otro módulo de esa carpeta.
Nombre `llm_provider_errors.py` (no `exceptions.py` a secas) para que quede
inequívoco, dentro de una carpeta compartida por varios Protocols, a cuál
de ellos pertenece esta jerarquía — la convención de `infrastructure/`
(`exceptions.py` único por módulo) no aplica igual porque `interfaces/` es
un único paquete con **varios** Protocols (`FeedCollector`, `LLMProvider`,
en el futuro `EmailDraftRepository`), no un módulo por proveedor.

`LLMInfrastructureError` (la raíz) **no se mueve**: `application/` no la
importa hoy (ni `analyze_job_post.py` ni `generate_application_email.py`
la capturan; capturan las dos subclases directamente). Queda como decisión
de `llm-agent` (fuera de este ADR, ver "Migration impact") si la conserva
en `infrastructure/llm/exceptions.py` como raíz interna únicamente para
agrupar futuras excepciones de infraestructura que jamás cruzan hacia
`application/` (p. ej. un fallo interno específico del SDK que
`OpenAIProvider` decida no exponer), o si la retira por quedar sin
subclases reales. Cualquiera de las dos es válida y no requiere un ADR
nuevo — es un detalle interno de infraestructura que no afecta el
dependency rule.

Las clases movidas quedan, en su nueva ubicación, como subclases directas
de `Exception` (no de ninguna clase nueva agregada por este ADR) —
preservan exactamente la relación de hermandad que ya tenían entre sí en
`infrastructure/`, solo que ya no comparten una raíz común con
`LLMInfrastructureError`, que se queda atrás. No se introduce ninguna
clase nueva de agrupación en `application/` para evitar una jerarquía
especulativa sin consumidor real (YAGNI) — los dos use cases ya capturan
la tupla de ambas explícitamente, nunca un supuesto ancestro común.

`app/infrastructure/llm/exceptions.py` pasa a definir sus clases
heredando desde el nuevo archivo (trabajo de `llm-agent`, no de este ADR —
ver "Migration impact"):

```python
from app.application.interfaces.llm_provider_errors import (
    LLMProviderError as _LLMProviderErrorBase,  # nombre orientativo, decide llm-agent
)
```

o, más simplemente, que `llm-agent` decida no re-declarar clases locales en
absoluto y que `OpenAIProvider`/`AnthropicProvider` levanten directamente
las clases de `app.application.interfaces.llm_provider_errors` — cualquiera
de las dos formas es una decisión mecánica de `llm-agent`, fuera de mi
ownership.

### 2. `CVInfrastructureError` → `app/application/cv/cv_catalog_errors.py` (nuevo archivo)

Se mueve únicamente la raíz (mismo nombre, mismo docstring de negocio
salvo el ajuste de la referencia a "un futuro use case `SelectBestCV`" —
ver nota más abajo), a un archivo nuevo en `application/cv/` — ownership
`cv-matching-agent`, igual que `cv_catalog.py` (el Protocol `CVCatalog` al
que esta excepción documenta como parte de su contrato de `get_summary`/
`list_cvs`).

Las cuatro subclases concretas (`CVCatalogNotFoundError`,
`CVCatalogMalformedError`, `CVFileNotFoundError`, `CVNotFoundError`) **no**
se mueven: ningún módulo de `application/` las importa por nombre hoy
(solo la raíz, vía `generate_application_email.py` y
`app/presentation/api/routes/jobs.py`) — se quedan en
`app/infrastructure/cv/exceptions.py`, ahora heredando de la raíz movida en
vez de la que se queda atrás (trabajo de `cv-matching-agent`, ver
"Migration impact").

**Por qué `application/cv/`, no `application/interfaces/` (fijando el
criterio para casos futuros):** el Protocol `CVCatalog` al que esta
excepción pertenece ya vive en `application/cv/`, no en
`application/interfaces/`, por la razón que `ADR-001` (nota 3) y
`docs/architecture/clean-architecture-skeleton.md` (nota 1) ya fijaron:
tanto el consumidor "nativo" del Protocol (`CVMatcher`, en
`application/cv/matcher.py`) como su implementación
(`FilesystemCVRepository`, en `infrastructure/cv/`) son ownership
end-to-end de `cv-matching-agent`. Que `GenerateApplicationEmail`
(`backend-engineer`) también consuma `CVCatalog.get_summary()` y por tanto
también necesite capturar `CVInfrastructureError` no cambia esa
propiedad — es un segundo consumidor externo del mismo contrato, análogo a
cómo `AnalyzeJobPost`/`SelectBestCV` (`llm-agent`/`cv-matching-agent`)
consumen `JobRepository` sin que eso mueva `JobRepository` fuera de
`domain/repositories/` (ownership `domain-engineer`). Poner
`CVInfrastructureError` en `application/interfaces/` (ownership
`backend-engineer`) obligaría a `cv-matching-agent` a coordinar con
`backend-engineer` cada vez que necesite ajustar la jerarquía de su propio
catálogo — exactamente el acoplamiento cruzado que ADR-001 ya evitó para
el Protocol mismo.

Nota de higiene textual (no es un cambio de comportamiento): el docstring
original de `CVInfrastructureError` decía *"quien la capture (un futuro use
case `SelectBestCV`, fuera de este ownership)"* — referencia ya desactualizada
incluso antes de este ADR (`SelectBestCV` no la importa; `GenerateApplicationEmail`
sí). El skeleton nuevo actualiza esa frase a los consumidores reales
conocidos hoy, sin alterar el nombre, la jerarquía pretendida ni ningún
mensaje de excepción — es documentación, no comportamiento.

### 3. `docs/architecture/clean-architecture-skeleton.md`

Se actualiza (mismo criterio que se usó para reflejar ADR-003/ADR-004/
ADR-005 en su momento):

- Nota 1 (Protocols): se agrega un párrafo aclarando que el mismo criterio
  de ownership de Protocol aplica a las excepciones que documentan su
  contrato informal ("raises"), con referencia a este ADR.
- Árbol de carpetas: `application/interfaces/` gana
  `llm_provider_errors.py`; `application/cv/` gana `cv_catalog_errors.py`.
- Nota 7 (LLMProvider/repos de Fases 3-6): se agrega una línea señalando
  que `LLMProviderError`/`LLMResponseValidationError` ahora viven en
  `application/interfaces/llm_provider_errors.py` (no en
  `infrastructure/llm/exceptions.py`, que conserva la raíz interna y,
  opcionalmente, subclases futuras que no cruzan el boundary), referenciando
  este ADR.
- Mapeo ownership → carpeta: sin cambios de fila (ambos archivos nuevos
  caen bajo ownership ya existente — `backend-engineer` para
  `application/interfaces/**`, `cv-matching-agent` para
  `application/cv/**`).

## Alternatives

### A. Dejar el import `application/` → `infrastructure/` como excepción documentada al dependency rule

Ya evaluada y **rechazada por decisión explícita del usuario** antes de
este ADR — no se reabre. Se registra solo para que quede trazado por qué
existe la opción B como la única desarrollada: hubiera sido la opción de
menor esfuerzo mecánico (cero archivos nuevos, cero cambios de imports),
pero perpetúa la violación literal del dependency rule
(`Infrastructure → Application interfaces → Domain`, nunca al revés) que
`code-reviewer` ya marcó como hallazgo, y sienta un precedente de "está bien
que `application/` importe de `infrastructure/` si es solo una excepción",
que es exactamente el tipo de excepción no controlada que
`ENGINEERING_STANDARDS.md` §2 busca prevenir.

### B'. Mover la jerarquía completa de excepciones (raíz + todas las subclases) de cada módulo, no solo lo que se importa hoy

- **Trade-off:** una única mudanza total sería más fácil de razonar ("todo
  `llm/exceptions.py` vive ahora en `application/`") y evitaría que
  `infrastructure/` tenga una raíz (`LLMInfrastructureError`) potencialmente
  huérfana de subclases reales.
- **Rechazada:** mueve código que hoy no cruza el dependency boundary
  (`CVCatalogNotFoundError`, `CVFileNotFoundError`, etc. — ningún módulo de
  `application/` las importa) sin necesidad real, violando YAGNI. También
  encierra en `application/` detalles que son legítimamente de
  infraestructura (p. ej. que un archivo YAML no exista en disco es un
  concepto 100% de `infrastructure/cv/`, no algo que `application/` deba
  conocer por nombre salvo por la raíz genérica que sí captura). Mover solo
  lo que se importa hoy mantiene la superficie movida mínima y auditable.

### C. Un único paquete `application/exceptions/` compartido para todas las excepciones de infraestructura promovidas, en vez de colgarlas del Protocol correspondiente

- **Trade-off:** una sola carpeta que buscar para "toda excepción que cruza
  el boundary", en vez de tener que saber a qué Protocol pertenece cada una.
- **Rechazada:** repite el error que `ADR-001` alternativa B ya rechazó para
  Protocols ("un único `interfaces/` compartido por domain y application") —
  pierde la señal semántica de qué excepción pertenece a qué contrato y, más
  importante, vuelve a mezclar ownership: `CVInfrastructureError` terminaría
  en una carpeta sin dueño claro entre `backend-engineer` y
  `cv-matching-agent`, el mismo problema de fondo que este ADR resuelve
  fijando "la excepción vive donde vive su Protocol".

## Trade-offs

| | Excepción vive junto a su Protocol (elegido) | Un paquete central `application/exceptions/` |
|---|---|---|
| Ownership sin ambigüedad | Sí (mismo dueño que el Protocol) | No (¿quién es dueño de la carpeta?) |
| Coherente con ADR-001 (criterio de Protocol location) | Sí, extensión directa | No, introduce un criterio nuevo y distinto |
| Descubribilidad ("¿qué excepciones puede lanzar este Protocol?") | Alta (archivo hermano) | Media (hay que buscar en otra carpeta) |
| Cantidad de archivos nuevos | 2 (uno por Protocol afectado) | 1 |

## Risks

- **Riesgo bajo, mecánico:** este ADR crea los dos archivos skeleton
  (`llm_provider_errors.py`, `cv_catalog_errors.py`) con las clases movidas,
  pero **no** actualiza `app/infrastructure/llm/exceptions.py`,
  `app/infrastructure/cv/exceptions.py`, ni los imports en
  `analyze_job_post.py`/`generate_application_email.py` — eso es trabajo de
  `llm-agent`, `cv-matching-agent` y `backend-engineer` respectivamente
  (instrucción explícita de esta tarea). Hasta que esos tres agentes
  ejecuten su parte, el código sigue importando desde `infrastructure/`
  (el hallazgo de `code-reviewer` sigue abierto en el código real, aunque
  la decisión arquitectónica ya está fijada). Mitigación:
  `orchestrator` debe asignar explícitamente esas tres tareas mecánicas
  antes de considerar el hallazgo `MEDIUM` cerrado.
- **Riesgo medio, deuda técnica adicional descubierta al escribir este ADR
  (no reportada en el hallazgo original, `DEBT-ADR006-01`):**
  `app/presentation/api/routes/jobs.py` (capa `Presentation`, ownership
  `backend-engineer`) también importa directamente
  `from app.infrastructure.cv.exceptions import CVInfrastructureError` y
  `from app.infrastructure.llm.exceptions import LLMProviderError,
  LLMResponseValidationError` para traducirlas a códigos HTTP. Esto es
  `Presentation → Infrastructure`, una violación del dependency rule
  todavía más directa que la original (`Presentation` debería depender
  solo de `Application`/`Domain`, nunca de `Infrastructure`). **Impacto:**
  el mismo `MEDIUM` de fondo existe también en `presentation/`, no solo en
  `application/`. **Por qué no se corrige en este ADR:** `jobs.py` no fue
  parte del hallazgo original de `code-reviewer` que definió el alcance de
  esta tarea, y no es ownership de `architect` editarlo. **Solución
  recomendada:** una vez que `backend-engineer` actualice los imports de
  los dos use cases para consumir `application/interfaces/llm_provider_errors.py`
  y `application/cv/cv_catalog_errors.py`, debe actualizar también
  `jobs.py` para importar desde esas mismas ubicaciones nuevas — resuelve
  ambos hallazgos con el mismo movimiento, sin trabajo adicional real.
  `orchestrator` debe incluir `jobs.py` explícitamente en el alcance de esa
  tarea de `backend-engineer`.
- **Riesgo medio, deuda técnica adicional descubierta en la revisión de
  `code-reviewer` sobre este mismo ADR (`DEBT-ADR006-02`), caso análogo a
  `DEBT-ADR006-01` pero para LinkedIn:** `app/presentation/api/routes/scrape.py`
  (líneas 43-46, capa `Presentation`, ownership `backend-engineer`) importa
  directamente `app.infrastructure.linkedin.exceptions` — la misma
  categoría de violación `Presentation → Infrastructure` que
  `DEBT-ADR006-01`, confirmada también por el propio docstring de
  `app/application/services/candidate_filter.py`, que la cita como
  "precedente idéntico en Fase 2". **Impacto:** el patrón que este ADR
  corrige para LLM/CV tiene un tercer caso vivo en el módulo de LinkedIn,
  fuera del alcance de esta tarea (que se limitó a los dos casos
  reportados por `code-reviewer` en su hallazgo `MEDIUM` original). **Por
  qué no se corrige en este ADR:** ownership de `linkedin-agent`
  (`app/infrastructure/linkedin/**`) y `backend-engineer`
  (`app/presentation/**`), fuera de mi ownership resolver el mecanismo de
  imports directamente, y decidir si aplica el mismo criterio (mover a un
  archivo neutral en `application/`) requeriría evaluar primero si algún
  use case de `application/` captura esas excepciones por nombre hoy —
  no se verificó como parte de esta tarea. **Solución recomendada:**
  `orchestrator` asigna a `architect` una evaluación puntual (mismo
  criterio de este ADR) si/cuando se decida cerrar este caso, o lo acepta
  como deuda técnica documentada si el volumen de imports directos desde
  `presentation/`/`application/` hacia `infrastructure/linkedin/` es bajo.
- **Riesgo bajo:** si `llm-agent` decide retirar `LLMInfrastructureError`
  (la raíz) por quedar sin subclases reales después de esta migración, y
  más adelante necesita reintroducir una raíz interna para una subclase
  nueva que no deba cruzar a `application/`, tendría que recrearla — costo
  bajo, es una clase de una línea sin lógica.
- **Riesgo nulo de violar el dependency rule por este ADR en sí** — ambos
  archivos nuevos viven en `application/`, sin importar nada de
  `infrastructure/` ni de ningún SDK externo; son los archivos que se
  actualizan fuera de este ADR (`infrastructure/llm/exceptions.py`,
  `infrastructure/cv/exceptions.py`) los que pasan a importar *hacia*
  `application/`, que es la dirección permitida
  (`Infrastructure → Application interfaces → Domain`).

## Migration impact

- `llm-agent` (fuera de este ADR): actualiza
  `app/infrastructure/llm/exceptions.py` para que `LLMProviderError`/
  `LLMResponseValidationError` hereden de (o sean re-exportadas desde)
  `app.application.interfaces.llm_provider_errors`, decide qué hacer con
  `LLMInfrastructureError` (conservarla como raíz interna sin uso hoy, o
  retirarla), y actualiza `anthropic_provider.py`/cualquier otro provider
  concreto si sus imports cambian de módulo.
- `cv-matching-agent` (fuera de este ADR): actualiza
  `app/infrastructure/cv/exceptions.py` para que `CVCatalogNotFoundError`,
  `CVCatalogMalformedError`, `CVFileNotFoundError`, `CVNotFoundError`
  hereden de `app.application.cv.cv_catalog_errors.CVInfrastructureError`
  en vez de la clase local que se retira.
- `backend-engineer` (fuera de este ADR): actualiza los imports en
  `app/application/use_cases/analyze_job_post.py`,
  `app/application/use_cases/generate_application_email.py`, y
  `app/presentation/api/routes/jobs.py` (ver `DEBT-ADR006-01`) para apuntar
  a `app.application.interfaces.llm_provider_errors` y
  `app.application.cv.cv_catalog_errors` en vez de
  `app.infrastructure.*`. También actualiza, en el mismo movimiento, el
  docstring de `app/application/interfaces/llm_provider.py` (líneas 20-29),
  que hoy documenta por nombre que `LLMProviderError`/
  `LLMResponseValidationError` "viven en `app.infrastructure.llm.exceptions`
  (todavía no existe)" — ambas partes de esa frase quedan desactualizadas
  (el archivo ya existe, y tras esta migración la ubicación real es
  `application/interfaces/llm_provider_errors.py`).
- `cv-matching-agent` (fuera de este ADR, además de lo ya listado arriba):
  actualiza también el docstring de `app/application/cv/cv_catalog.py`
  (líneas 39-40 y 54-55), que hoy referencia por nombre
  `app.infrastructure.cv.exceptions.CVInfrastructureError`/`CVNotFoundError`
  — tras la migración, `CVInfrastructureError` vive en
  `app.application.cv.cv_catalog_errors` (`CVNotFoundError`, subclase
  concreta, sigue en `app.infrastructure.cv.exceptions`, sin cambios).
- Nota de higiene textual adicional (detectada por `code-reviewer` sobre
  este mismo ADR, no accionable dentro de mi ownership): el docstring de
  `app/application/services/candidate_filter.py` describe hoy el patrón
  previo a esta decisión como "ya aceptado explícitamente por ADR-005 y con
  precedente idéntico en Fase 2" (citando `app/presentation/api/routes/scrape.py`)
  — ese razonamiento queda contradicho por este ADR (ver "Alternatives A" y
  "Problem": ADR-005 nunca aceptó explícitamente ese patrón, solo lo dejó
  sin resolver). `backend-engineer`/`llm-agent` deben ajustar ese párrafo al
  actualizar los imports de `analyze_job_post.py`/`generate_application_email.py`,
  ya que `candidate_filter.py` es citado desde el docstring de
  `analyze_job_post.py` como referencia cruzada.
- `testing-agent` (fuera de este ADR, si aplica): los tests que hoy
  importan `LLMProviderError`/`LLMResponseValidationError` desde
  `app.infrastructure.llm.exceptions`
  (`tests/unit/application/use_cases/test_analyze_job_post.py`,
  `tests/unit/application/use_cases/test_generate_application_email.py`,
  `tests/unit/presentation/api/routes/test_jobs.py`) deberían actualizar su
  import al nuevo módulo una vez que `llm-agent` complete su parte —
  `tests/unit/infrastructure/llm/test_anthropic_provider.py` puede seguir
  importando desde `app.infrastructure.llm.exceptions` sin cambios, ya que
  ejercita la capa de infraestructura directamente y esas clases (movidas
  o re-exportadas) seguirán siendo importables desde ahí mientras
  `llm-agent` mantenga la re-exportación.
- No hay cambios de esquema SQL ni de comportamiento runtime en este ADR —
  es una reubicación de clases (mismo nombre, mismo mensaje, misma
  jerarquía de hermandad), no un cambio de qué se lanza ni cuándo.
