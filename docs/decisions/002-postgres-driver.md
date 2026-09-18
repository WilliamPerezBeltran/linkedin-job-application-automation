# ADR-002: PostgreSQL driver for SQLAlchemy — psycopg2-binary vs asyncpg

## Status

Accepted.

## Problem

`ROADMAP.md` Fase 0 punto 2 y `ENGINEERING_STANDARDS.md` requieren inicializar
`pyproject.toml` con SQLAlchemy + un driver de PostgreSQL, dejando explícitamente abierta la
elección entre `psycopg2-binary` (driver síncrono) y `asyncpg` (driver async) — ver
`ROADMAP.md`: *"sqlalchemy, psycopg2-binary (o asyncpg)"*. Esta decisión afecta transversalmente
cómo se escriben `JobRepository`/`ApplicationRepository`, las migraciones de Alembic, y si las
rutas FastAPI y los use cases deben ser `async def` o `def`. No la implemento en esta tarea (eso
corresponde al orchestrator al armar `pyproject.toml` y al database-agent al implementar
`app/infrastructure/database/**` en la Fase 1) — solo decido y documento el criterio.

## Current approach

Ninguno — no hay código de base de datos todavía (Fase 1 aún no comienza).

## Proposed approach

**Usar `psycopg2-binary` (driver síncrono) con SQLAlchemy en modo síncrono (engine y `Session`
síncronos, no `AsyncEngine`/`AsyncSession`).**

Razones, en el orden de prioridad definido por `ENGINEERING_STANDARDS.md` §39
(Correctness > Security > Maintainability > Testability > Simplicity > Observability > Performance):

1. **Perfil de carga real del proyecto.** Es una app local, de un solo usuario (el propio dueño
   del repo), sin requisito de servir múltiples usuarios concurrentes ni alto volumen de tráfico
   HTTP simultáneo. El pipeline (`CLAUDE.md`: scrape → analyze → CV match → email → draft) es
   inherentemente secuencial por diseño de la máquina de estados (`SCRAPED → ANALYZED → ... →
   SENT`, "evita reprocesar la misma oferta más de una vez"), no un flujo de alta concurrencia
   que se beneficie de un event loop async para la base de datos.
2. **El resto de la I/O externa del sistema ya es predominantemente síncrona por naturaleza o por
   elección:**
   - Playwright: se usará su API síncrona (más simple de razonar y testear para un scraper de un
     solo flujo, sin necesidad de paralelismo de páginas).
   - LLM (OpenAI/Anthropic SDKs): se invocan de forma síncrona en los `Provider` (`LLMProvider`
     Protocol, `docs/agents/AGENTS.md` §10) — un caso de uso llama al LLM y espera la respuesta
     antes de continuar; no hay fan-out concurrente de llamadas al LLM en el diseño actual.
   - Gmail API: creación de un draft a la vez, tras revisión humana — tampoco concurrente.
   Si el driver de PostgreSQL fuera async pero todo lo demás es síncrono, los use cases quedarían
   con una mezcla `async def` (por la DB) llamando a colaboradores síncronos (Playwright, LLM,
   Gmail) — esto obliga a envolver llamadas bloqueantes en `run_in_executor`/`asyncio.to_thread`
   en cada punto de integración, agregando complejidad accidental sin beneficio real de
   throughput.
3. **Alembic** funciona de forma síncrona por defecto y es la ruta mejor documentada y con menos
   fricción; usar `asyncpg` obliga a configurar `run_sync` dentro de `env.py` de Alembic —
   complejidad extra sin necesidad real (ENGINEERING_STANDARDS §31 "No Overengineering": *"¿Es
   realmente necesario? ¿Existe una solución estándar más sencilla?"*).
4. **Testabilidad.** ENGINEERING_STANDARDS §20-21 exige que los use cases se puedan testear con
   `InMemoryJobRepository`/fakes sin tocar PostgreSQL. Un repositorio síncrono es trivialmente más
   simple de fakear e inspeccionar en tests unitarios (sin `pytest-asyncio`, sin `asyncio.run`,
   sin fixtures async) — reduce fricción de testing, que es una prioridad explícita por encima de
   performance en la jerarquía de §39.
5. **FastAPI no exige async.** FastAPI soporta rutas `def` síncronas de forma nativa (las ejecuta
   en un threadpool), así que no perder capacidades del framework por elegir un driver síncrono
   para la capa de persistencia. Las rutas en `app/presentation/api/routes/` pueden ser `def`
   normales que invocan use cases síncronos.

   **Nota de implementación para `database-agent` (Fase 1):** una `SQLAlchemy Session` síncrona no
   es thread-safe y no debe compartirse entre threads. Como FastAPI ejecuta las rutas `def` en un
   threadpool, `database-agent` debe seguir el patrón estándar "una `Session` por request" (crear
   y cerrar la sesión dentro de la dependencia inyectada vía `Depends`, nunca una `Session` global
   o de módulo reutilizada entre requests). Esto es el patrón por defecto documentado por
   SQLAlchemy/FastAPI, no una complejidad adicional de esta decisión.

## Alternatives

### A. `asyncpg` + SQLAlchemy async (`AsyncEngine`/`AsyncSession`)

- **Trade-off:** mejor throughput bajo alta concurrencia de conexiones a PostgreSQL, y permite que
  FastAPI aproveche completamente su event loop async de punta a punta si en el futuro el proyecto
  sirve a múltiples usuarios concurrentes o necesita procesar muchos jobs en paralelo.
- **Costo:** todos los Protocols de repositorio (`JobRepository`, `ApplicationRepository`) pasarían
  a tener métodos `async def`, propagando `async`/`await` hacia arriba por todos los use cases y
  las rutas de FastAPI que los invocan — un cambio transversal, no aislado a Infrastructure.
  Alembic requiere configuración adicional para migraciones async. Los fakes de test necesitan
  `pytest-asyncio` y métodos async, aumentando la fricción de escribir tests unitarios simples.
- **Quedó descartada para el MVP** por no existir hoy un requisito real de concurrencia
  (ENGINEERING_STANDARDS §37: "Optimizar solamente después de medir... No realizar optimizaciones
  prematuras").

### B. `psycopg` v3 (driver moderno, soporta modo síncrono y async desde el mismo paquete)

- **Trade-off:** driver más nuevo y mejor mantenido a largo plazo que `psycopg2`, con soporte
  opcional para async sin cambiar de paquete si más adelante se necesita. Requiere verificar
  compatibilidad con la versión de SQLAlchemy que se fije en `pyproject.toml` (SQLAlchemy 2.x
  soporta `psycopg` v3 vía el dialecto `postgresql+psycopg`).
- **Por qué no se elige como decisión principal ahora:** `psycopg2-binary` es la opción más probada,
  con más años de uso en producción y la que `ROADMAP.md` menciona explícitamente como opción
  por defecto junto a `asyncpg`; introducir `psycopg` v3 sin que el roadmap lo mencione sería una
  desviación no solicitada. Se deja documentado como alternativa viable si en el futuro se quiere
  una ruta de migración a async más suave sin cambiar de familia de driver — ver "Migration impact".

## Trade-offs

| | psycopg2-binary (elegido) | asyncpg |
|---|---|---|
| Complejidad inicial | Baja | Media-alta (propaga async a use cases/rutas/Alembic) |
| Testabilidad unitaria | Alta (fakes síncronos simples) | Requiere `pytest-asyncio` |
| Throughput bajo concurrencia alta | Menor | Mayor |
| Encaje con Playwright/LLM/Gmail síncronos | Directo | Requiere puentes async/sync |
| Madurez/soporte | Muy alta (años en producción) | Alta, pero más joven |
| Alineado con perfil de carga actual (1 usuario, local) | Sí | Sobredimensionado |

## Risks

- **Riesgo medio a largo plazo:** si el proyecto evoluciona hacia servir múltiples usuarios
  concurrentes (fuera del alcance actual — CLAUDE.md fija explícitamente "no microservicios,
  no Kubernetes... todo corre localmente... hasta que exista una necesidad real de escalar"),
  el driver síncrono limitará el throughput de la capa HTTP bajo carga concurrente real. Mitigación:
  la interfaz `JobRepository`/`ApplicationRepository` (Protocol) aísla esta decisión en
  `app/infrastructure/database/**`; migrar a async no debería requerir cambios en Domain, y el
  costo se limita a Application (firmas `async def`) e Infrastructure.
- **Riesgo bajo:** `psycopg2-binary` es el paquete "binary" (incluye libpq embebido) recomendado
  para desarrollo pero no para producción según la documentación oficial de psycopg2 (en producción
  se recomienda compilar `psycopg2` desde código fuente para evitar conflictos de versión de
  libpq). Dado que el proyecto corre localmente y no se despliega a un entorno productivo
  gestionado externamente (CLAUDE.md: "todo corre localmente"), este riesgo se considera aceptable
  para el MVP. Si más adelante se dockeriza para distribución, evaluar cambiar a `psycopg2`
  (no binary) dentro de la imagen Docker.

## Migration impact

Si en el futuro se necesita async real:

1. Cambiar el dialecto del `DATABASE_URL` (hoy `postgresql://...` en `.env.example`) al prefijo
   correspondiente al driver elegido (`postgresql+asyncpg://...` o `postgresql+psycopg://...` en
   modo async) — el database-agent debe actualizar `.env.example` cuando implemente Fase 1,
   independientemente de esta decisión, ya que SQLAlchemy requiere el dialecto explícito en la
   URL (`postgresql://` sin sufijo apunta a `psycopg2` por defecto, que es justamente lo que
   confirma este ADR, así que no requiere cambio inmediato).
2. Cambiar `create_engine`/`Session` por `create_async_engine`/`AsyncSession` en
   `app/infrastructure/database/session.py`.
3. Convertir los métodos de `JobRepository`/`ApplicationRepository` (Protocol en
   `app/domain/repositories/`) y sus implementaciones SQLAlchemy a `async def`.
4. Propagar `async`/`await` hacia los use cases que dependan de esos repositorios y hacia las
   rutas FastAPI correspondientes.
5. Configurar Alembic para migraciones async (`run_sync` en `env.py`) o mantener un engine
   síncrono exclusivo para migraciones (patrón común: Alembic corre sync incluso en apps que usan
   SQLAlchemy async en runtime).
6. Actualizar los fakes de test (`InMemoryJobRepository`, etc.) a métodos `async def` y agregar
   `pytest-asyncio` a `pyproject.toml`.

Este es un cambio de alcance medio pero aislado por la Dependency Inversion ya exigida por
`ENGINEERING_STANDARDS.md` §9: Domain y la mayoría de Application no conocen SQLAlchemy, solo el
Protocol — el costo real está concentrado en Infrastructure y en la firma de los métodos de los
Protocols, no en reescribir reglas de negocio.
