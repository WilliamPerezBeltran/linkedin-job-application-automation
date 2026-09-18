# linkedin-job-application-automation

Automated workflow for collecting LinkedIn job posts, analyzing opportunities with AI,
generating application emails, and selecting the appropriate CV.

See [`CLAUDE.md`](./CLAUDE.md) for the full project description, [`ROADMAP.md`](./ROADMAP.md)
for the phase-by-phase plan, [`ENGINEERING_STANDARDS.md`](./ENGINEERING_STANDARDS.md) for
mandatory engineering standards, [`TOKEN_OPTIMIZATION.md`](./TOKEN_OPTIMIZATION.md) for LLM
cost/latency strategy, and [`docs/agents/AGENTS.md`](./docs/agents/AGENTS.md) for the
specialized-agent architecture used to build this project.

## Status

Fases 0–10 del `ROADMAP.md` implementadas: setup, base de datos, LinkedIn Collector (solo feed),
Job Analyzer (LLM), CV Matcher, Email Generator (LLM), API + dashboard de revisión manual,
integración con Gmail (creación de drafts, nunca envío automático), estado `SENT` +
trazabilidad, scheduler diario, y esta tanda de Docker/CI/tests. El pipeline completo
(`scrape → analyze → CV match → email → draft de Gmail → envío manual`) está operativo, siempre
con puntos de revisión humana explícitos (nunca de punta a punta sin intervención).

## Pipeline

```
LinkedIn Feed (solo /feed/, nunca perfiles/ofertas/links externos)
   │  Playwright
   ▼
Job Collector → dedup por content_hash → PostgreSQL (status=SCRAPED)
   │
   ▼
Job Analyzer (LLM, filtro barato antes) → is_job, category, skills, email
   │                                          status=ANALYZED/NOT_RELEVANT/RELEVANT
   ▼
CV Matcher (determinístico por skills) → recommended_cv, matching/missing skills
   │                                          status=CV_SELECTED
   ▼
Email Generator (LLM) → subject + body
   │                                          status=EMAIL_GENERATED
   ▼
Dashboard de revisión manual (React) → editar email, aprobar
   │
   ▼
Gmail API → crea Draft con CV adjunto (nunca envía)
   │                                          status=DRAFT_CREATED
   ▼
Usuario revisa y hace Send manualmente desde Gmail → "Mark as Sent" en el dashboard
                                                       status=SENT
```

El scheduler diario (Fase 9) corre `Collect → Analyze → SelectCV → GenerateEmail`
automáticamente, dejando todo en `EMAIL_GENERATED` listo para revisión — nunca crea drafts ni
envía nada por sí mismo.

## Requisitos

* Python >= 3.11
* Node >= 20 (frontend — dashboard React)
* Docker + Docker Compose (para levantar PostgreSQL, y opcionalmente la app completa)
* Una cuenta de Google Cloud con la Gmail API habilitada (para crear drafts, Fase 7)
* Credenciales de LinkedIn propias (uso personal, ver advertencia de Términos de Servicio en
  `CLAUDE.md`)
* Una API key de Anthropic (proveedor LLM usado por defecto) u OpenAI

## Setup local (sin Docker, para desarrollo)

```bash
# 1. Crear y activar un entorno virtual
python3 -m venv .venv
source .venv/bin/activate

# 2. Instalar dependencias (runtime + desarrollo)
pip install -e ".[dev]"

# 3. Instalar el navegador de Playwright (requerido para el LinkedIn Collector, Fase 2)
#    Este paso NO se ejecuta automáticamente — descarga binarios pesados (~300 MB) y debe
#    correrlo cada desarrollador de forma manual:
playwright install chromium

# 4. Copiar variables de entorno y completar con valores reales
cp .env.example .env

# 5. Levantar PostgreSQL local (ver "Con Docker" más abajo para levantar todo el stack)
docker compose up -d postgres

# 6. Aplicar las migraciones
alembic upgrade head

# 7. Correr la API + dashboard
uvicorn app.main:app --reload

# 8. (Otra terminal, opcional) Correr el scheduler diario
python -m app.presentation.scheduler.jobs

# 9. (Otra terminal) Frontend
cd frontend
npm install
npm run dev
```

Verificación rápida de que todo está en orden:

```bash
ruff check .
mypy app
pytest --cov=app --cov-report=term-missing
```

## Con Docker (stack completo desde cero)

```bash
cp .env.example .env   # completar con valores reales antes de levantar

docker compose up -d
```

Esto levanta tres servicios (ver `docker-compose.yml`):

* `postgres` — PostgreSQL 16, con healthcheck.
* `app` — corre `alembic upgrade head` y después `uvicorn app.main:app` (puerto `8000`).
* `scheduler` — corre `python -m app.presentation.scheduler.jobs` (pipeline diario automático).

`app`/`scheduler` montan `./cvs`, `./config` y `./secrets` (credenciales/tokens locales, ver
sección de Gmail más abajo) como volúmenes — no hace falta reconstruir la imagen para actualizar
CVs o rotar credenciales.

El frontend **no** corre dentro de este `docker-compose.yml` (ver `ROADMAP.md` Fase 10, que solo
pide `app + postgres`) — se sirve por separado en desarrollo (`npm run dev` dentro de
`frontend/`) o se construye estáticamente (`npm run build`, sirviendo `frontend/dist/` con
cualquier servidor de archivos estáticos) para un despliegue real.

Para correr solo la API sin el scheduler: `docker compose up -d postgres app`.

## Variables de entorno

Ver [`.env.example`](./.env.example) para la lista completa y comentada. Resumen por área:

| Variable | Descripción |
|---|---|
| `DATABASE_URL`, `POSTGRES_USER/PASSWORD/DB/PORT` | Conexión a PostgreSQL. Dentro de Docker Compose, `app`/`scheduler` sobreescriben `DATABASE_URL` para apuntar al hostname interno `postgres` (ver comentarios en `docker-compose.yml`). |
| `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `LLM_PROVIDER` | Proveedor LLM (Job Analyzer + Email Generator). `ANTHROPIC_MODEL`/`ANTHROPIC_EMAIL_MODEL` controlan qué modelo usa cada etapa (ver `TOKEN_OPTIMIZATION.md`). |
| `LINKEDIN_EMAIL`, `LINKEDIN_PASSWORD` | Solo para el primer login manual/controlado que genera `storage_state.json` (Fase 2) — nunca se guardan en texto plano de forma permanente, ver `app/infrastructure/linkedin/session.py`. |
| `GMAIL_CREDENTIALS_PATH`, `GMAIL_TOKEN_PATH` | Rutas a `credentials.json`/`token.json` (Fase 7, Gmail OAuth) — ver pasos manuales abajo. |
| `SCHEDULER_DAILY_HOUR` | Hora del día (0–23) a la que corre el pipeline automático (Fase 9). |

## Pasos manuales (no automatizados por diseño)

Estos pasos requieren intervención humana — ninguno se puede automatizar de forma segura o
legítima, y así está documentado en cada fase correspondiente del `ROADMAP.md`:

1. **`playwright install chromium`** — descarga de binarios, cada desarrollador/máquina lo corre
   una vez (dentro de Docker ya está incluido en la imagen, ver `Dockerfile`).
2. **`.env` real** a partir de `.env.example` con credenciales propias — nunca se comitea.
3. **Primer login manual/controlado en LinkedIn** para generar `storage_state.json` (Fase 2, ver
   `app/infrastructure/linkedin/session.py`). Revisar los Términos de Servicio de LinkedIn antes
   de automatizar cualquier scraping — este proyecto solo lee el feed (`linkedin.com/feed/*`),
   nunca perfiles, ofertas individuales, mensajes ni conexiones.
4. **Credenciales OAuth 2.0 de Gmail** (`credentials.json`, Fase 7) — se generan manualmente en
   Google Cloud Console, nunca se automatiza su creación:
   1. Ir a <https://console.cloud.google.com/> y crear (o reusar) un proyecto.
   2. "APIs & Services" → "Library": habilitar la **Gmail API**.
   3. "APIs & Services" → "OAuth consent screen": tipo "External" (o "Internal" en Google
      Workspace), agregando el propio email como "test user" mientras la app no esté
      publicada/verificada.
   4. "APIs & Services" → "Credentials" → "Create Credentials" → "OAuth client ID" → tipo de
      aplicación **"Desktop app"** (no "Web application" — el flujo usa
      `InstalledAppFlow.run_local_server`, pensado para apps locales/CLI).
   5. Descargar el JSON generado y guardarlo como `credentials.json` en la raíz del repo (o la
      ruta que apunte `GMAIL_CREDENTIALS_PATH`) — **nunca comitearlo** (ya está en
      `.gitignore`). Con Docker, colocarlo en `./secrets/credentials.json`.
   6. Ejecutar cualquier flujo que cree un draft real por primera vez (p. ej. el botón "Create
      Draft" del dashboard, una vez que un `Job` llegó a `EMAIL_GENERATED`): se abre un navegador
      para el consentimiento OAuth. Tras autorizar, el token queda persistido en
      `GMAIL_TOKEN_PATH` (`token.json`, permisos `0600`, gitignored) para no repetir el
      consentimiento en corridas futuras.
   7. Scope usado: `https://www.googleapis.com/auth/gmail.compose` — el más restrictivo
      disponible que permite crear drafts con adjuntos (ver el docstring completo de
      `app/infrastructure/gmail/gmail_client.py` para la investigación de por qué no hay uno más
      chico). La aplicación **nunca** llama a ningún endpoint de envío (`users.messages.send`,
      `users.drafts.send`) — el envío final es 100% manual, desde Gmail.

## Cómo correr cada fase / flujo manualmente

* **Scrapear el feed una vez**: `POST /api/scrape` (o el botón correspondiente del dashboard).
* **Ver jobs por estado**: `GET /api/jobs?status=SCRAPED` (o cualquier otro `JobStatus`) — o la
  vista "Jobs" del dashboard.
* **Analizar / seleccionar CV / generar email / crear draft / marcar enviado**: desde el
  dashboard (`JobDetail`), o directamente contra la API:
  `POST /api/jobs/{id}/analyze`, `POST /api/jobs/{id}/generate-email`,
  `POST /api/jobs/{id}/create-draft`, `POST /api/applications/{id}/mark-sent`.
* **Ver el tablero de trazabilidad** (jobs por estado, aplicaciones enviadas por
  semana/categoría): vista "Dashboard" del frontend, o `GET /api/stats/dashboard`.
* **Correr el pipeline automático una vez** (sin esperar al cron diario), útil para probar:
  ```python
  from app.presentation.scheduler.jobs import _run_daily_pipeline_job
  _run_daily_pipeline_job()
  ```
  o simplemente arrancar `python -m app.presentation.scheduler.jobs` y esperar a
  `SCHEDULER_DAILY_HOUR`.

## Estructura del proyecto

Ver [`docs/architecture/clean-architecture-skeleton.md`](./docs/architecture/clean-architecture-skeleton.md)
para el árbol completo de carpetas por capas (`domain / application / infrastructure /
presentation`) y el mapeo de ownership por agente. Decisiones arquitectónicas relevantes están
documentadas como ADRs en [`docs/decisions/`](./docs/decisions/).

## Tests

```bash
pytest                                        # suite completa (unit + integration)
pytest -m unit                                # solo unit (sin dependencias externas)
pytest -m integration                         # solo integración (requiere PostgreSQL real)
pytest --cov=app --cov-report=term-missing    # con reporte de cobertura
cd frontend && npm run test -- --run          # suite del frontend
```

Los tests de integración se saltan limpiamente si no hay PostgreSQL disponible en
`DATABASE_URL`. Ningún test (unit o integración) llama a LinkedIn, Gmail ni a un proveedor LLM
reales — todos usan fakes/mocks (`docs/agents/AGENTS.md` sección 16).

## CI/CD

`.github/workflows/ci.yml` corre en cada push/PR, con cuatro jobs independientes: `lint`
(`ruff` + `mypy --strict`), `test` (`pytest` contra un servicio real de PostgreSQL provisto por
GitHub Actions), `frontend` (lint + test + build) y `docker-build` (build de la imagen, sin
publicarla).
