# Roadmap — linkedin-job-application-automation

Cada fase tiene un entregable verificable ("Done cuando...") y un agente responsable (ver [`docs/agents/AGENTS.md`](./docs/agents/AGENTS.md) y [`.claude/agents/`](./.claude/agents/)). No pasar a la siguiente fase sin poder demostrar la anterior funcionando. Ver arquitectura completa en [`CLAUDE.md`](./CLAUDE.md) y [`ENGINEERING_STANDARDS.md`](./ENGINEERING_STANDARDS.md) — las rutas de esta guía usan la estructura por capas (`domain/application/infrastructure/presentation`), no la estructura plana de versiones anteriores de este documento.

Transversal a **todas** las fases (no tienen fase propia):

* `testing-agent` — agrega/revisa tests en cada fase, no solo en la 10.
* `code-reviewer` — cada agente lo invoca al terminar su parte (ya configurado en cada `.claude/agents/*.md`).
* `security-agent` — especialmente en Fase 2 (credenciales LinkedIn) y Fase 7 (OAuth Gmail).
* `token-optimization-agent` — relevante desde la Fase 3 en adelante, una vez hay llamadas reales al LLM.

---

## Fase 0 — Setup del proyecto

**Agente:** `architect` (define el skeleton de capas) → `orchestrator` (resto del setup).

1. Crear el skeleton de Clean Architecture: `app/domain/`, `app/application/`, `app/infrastructure/`, `app/presentation/`, `tests/{unit,integration,e2e}/`, `prompts/`, `cvs/`, `migrations/`, `docs/{architecture,decisions}/` (ver estructura completa en `ENGINEERING_STANDARDS.md` §26).
2. Inicializar `pyproject.toml` con: `fastapi`, `uvicorn`, `sqlalchemy`, `psycopg2-binary` (o `asyncpg`), `alembic`, `playwright`, `pydantic`, `pydantic-settings`, `python-dotenv`, `pyyaml`, `pytest`, `httpx`, `ruff`, `mypy`, `pre-commit`.
3. `.env.example` y `.gitignore` ya existen en el repo — verificar que cubren todo lo nuevo que se agregue.
4. `docker-compose.yml` con un servicio `postgres`.
5. Playwright: `playwright install chromium`.

**Done cuando:** `docker-compose up -d postgres` levanta la base, `python -c "import fastapi, playwright, sqlalchemy"` no falla, y la estructura de carpetas por capas existe vacía.

---

## Fase 1 — Base de datos y modelos

**Agente:** `domain-engineer` (entidades + interfaces de repositorio) → `database-agent` (modelos SQLAlchemy, migraciones).

1. `domain-engineer`: entidades `Job`, `JobAnalysis`, `Application` y value objects (`JobId`, `ApplicationId`, `EmailAddress`) en `app/domain/entities/` y `app/domain/value_objects/`; interfaz `JobRepository` (`Protocol`) en `app/domain/repositories/`.
2. `database-agent`: `app/infrastructure/database/session.py` (engine + sesión SQLAlchemy), `app/infrastructure/database/sqlalchemy_models.py` con las tablas `jobs`, `job_analysis`, `applications` (ver esquema en `CLAUDE.md`), `content_hash` único en `jobs` para deduplicación desde el inicio, e implementación `SQLAlchemyJobRepository` en `app/infrastructure/database/repositories/`.
3. Alembic (`migrations/`): primera migración con las 3 tablas.
4. `.env`: `DATABASE_URL=postgresql://user:pass@localhost:5432/jobs_db`.
5. `testing-agent`: test que abra sesión, inserte un `Job` vía el repositorio y lo lea.

**Done cuando:** `alembic upgrade head` crea las tablas y el test de inserción/lectura pasa contra la interfaz `JobRepository`, no contra SQLAlchemy directamente.

---

## Fase 2 — LinkedIn Collector (solo feed)

**Agente:** `linkedin-agent`.

1. `app/infrastructure/linkedin/browser.py`: lanza Playwright (Chromium), reutilizando `storage_state` para no reautenticar cada vez.
2. `app/infrastructure/linkedin/session.py`: login inicial manual/controlado, guarda `storage_state.json` (gitignored).
3. `app/infrastructure/linkedin/feed.py`: navega a `linkedin.com/feed/` con **whitelist explícita de URLs permitidas**, scroll controlado, extrae autor/texto/timestamp/URL de cada post. Nunca `click` en un post ni navegación fuera de `/feed/`.
4. `app/infrastructure/linkedin/parser.py`: normaliza el HTML/texto a un dict limpio.
5. `app/infrastructure/linkedin/linkedin_feed_collector.py`: implementa la interfaz `FeedCollector` (definida en domain/application), calcula `content_hash = sha256(normalized_content)` y delega el guardado al `JobRepository` (upsert; si el hash ya existe, ignorar). Guarda cada post como `status=SCRAPED`.
6. `backend-engineer` (coordinación mínima): expone `POST /scrape` en `app/presentation/api/` que invoca el use case `CollectFeedPosts`, para correrlo a demanda mientras se desarrolla.

**Done cuando:** correr el collector una vez guarda N posts nuevos en `jobs`; correrlo de nuevo inmediatamente después no duplica filas (dedup funcionando), y `FeedCollector` es la única interfaz que el resto de la app conoce (Playwright no se filtra a domain/application).

⚠️ Antes de automatizar el login, revisar los Términos de Servicio de LinkedIn — este proyecto es para uso personal, sin scraping masivo ni evasión de controles anti-bot. `security-agent` debe revisar el manejo de `storage_state.json` antes de dar esta fase por cerrada.

---

## Fase 3 — Job Analyzer (LLM)

**Agente:** `llm-agent` (+ `domain-engineer` si falta algún campo en la entidad `JobAnalysis`).

1. `app/infrastructure/llm/openai_provider.py` / `anthropic_provider.py`: implementan la interfaz `LLMProvider` (`Protocol`, definida en domain/application) con el método `analyze_job`.
2. `prompts/job-analysis/v1.txt`: prompt versionado, con lista cerrada de categorías (Java, Python, AI/ML, Deep Learning, JavaScript/Node, Go, Elixir, Full Stack, Other).
3. Salida JSON estructurada validada con Pydantic antes de tocar el dominio:
   ```json
   {"is_job": true, "job_category": "software_engineering", "seniority": "senior",
    "skills": ["Java","Spring Boot","Kafka","AWS"], "email_addresses": [], "confidence": 0.93}
   ```
4. `application/use_cases/analyze_job_post.py` (`backend-engineer` o `llm-agent`, coordinar): orquesta `LLMProvider` + `JobRepository`, guarda el resultado y mueve `status` a `ANALYZED` o `NOT_RELEVANT`.
5. Aplica el **Candidate Filter** (reglas baratas por keywords, sin LLM) antes de llamar al proveedor — ver `TOKEN_OPTIMIZATION.md`.
6. `testing-agent`: tests con `FakeLLMProvider` y 4-5 posts de ejemplo fijos como fixtures — nunca llamar al LLM real en unit tests.

**Done cuando:** correr el analyzer sobre los posts scrapeados en Fase 2 llena `job_analysis` correctamente, respeta el filtro barato antes de gastar tokens, y mueve el `status` de cada job.

---

## Fase 4 — CV Matcher

**Agente:** `cv-matching-agent`.

1. `config/cvs.yaml` (o `app/infrastructure/cv/cvs.yaml`) con el mapeo categoría→archivo→skills.
2. PDFs reales en `cvs/<categoria>/` (gitignored — ver `.gitignore`).
3. `app/infrastructure/cv/filesystem_cv_repository.py`: carga y parsea el catálogo.
4. `app/application/cv/matcher.py`: matching determinístico por intersección de skills normalizadas (`len(set(job_skills) & set(cv_skills))`); fallback semántico opcional vía `LLMProvider` solo si el determinístico es insuficiente.
5. Guarda `recommended_cv`, `matching_skills`, `missing_skills`, `confidence` en `job_analysis`, mueve `status` a `CV_SELECTED`.
6. `testing-agent`: dado un job con skills `["Java","Spring Boot"]`, verificar que selecciona el CV de Java y no el de Python.

**Done cuando:** cada job en `ANALYZED`/`RELEVANT` termina con un `recommended_cv` sensato y explicable (skills que hicieron match vs. las que faltan).

---

## Fase 5 — Email Generator

**Agente:** `llm-agent` (usa el resumen de CV que provee `cv-matching-agent`, no el PDF completo).

1. `app/infrastructure/llm/*_provider.py`: método `generate_email` de la interfaz `LLMProvider`.
2. `prompts/email-generation/v1.txt`: prompt versionado — no inventar experiencia/tecnologías/empresas que no estén en el CV.
3. `application/use_cases/generate_application_email.py`: recibe `job_analysis` + resumen del CV seleccionado, pide `{"subject": "...", "body": "..."}` estructurado, guarda en `job_analysis`/`applications`, mueve `status` a `EMAIL_GENERATED`.
4. `testing-agent`: test con `FakeLLMProvider`, verificando que el JSON parseado tiene las claves esperadas.

**Done cuando:** para un job en `CV_SELECTED`, se genera subject+body coherente y queda persistido, sin inventar información fuera del CV.

---

## Fase 6 — API + Dashboard (revisión manual)

**Agente:** `backend-engineer` (API) + `frontend-agent` (UI).

1. `backend-engineer` — `app/presentation/api/routes/jobs.py`: `GET /api/jobs` (con filtro por status y paginación), `GET /api/jobs/{id}`, `POST /api/jobs/{id}/ignore`, `POST /api/jobs/{id}/analyze`, `POST /api/jobs/{id}/generate-email`. DTOs de request/response en `app/application/dto/`, nunca exponer entidades de dominio directamente.
2. `frontend-agent` — `frontend/`: lista de jobs nuevos con skills, CV recomendado y explicación del match, botón "Generate Email", vista previa de subject/body editable.
3. Permitir editar manualmente el subject/body generado antes de crear el draft (guardar la edición en `applications` vía la API, no en el cliente).

**Done cuando:** desde el navegador se puede ver un job, su CV recomendado, generar/editar el email y ver el preview — sin tocar la base de datos a mano.

---

## Fase 7 — Gmail integration (Drafts)

**Agente:** `gmail-agent`.

1. Credenciales OAuth 2.0 en Google Cloud Console (Gmail API habilitada), `credentials.json` (gitignored).
2. `app/infrastructure/gmail/gmail_client.py`: flujo OAuth, guarda `token.json` (gitignored) tras primera autorización.
3. `app/infrastructure/gmail/gmail_draft_repository.py`: implementa `EmailDraftRepository`, crea un **draft** (no envía) con `to`, `subject`, `body` y el PDF del CV adjunto.
4. `backend-engineer` (coordinación): `POST /api/jobs/{id}/create-draft` invoca el use case `CreateGmailDraft`, guarda `gmail_draft_id` en `applications`, mueve `status` a `DRAFT_CREATED`.
5. `frontend-agent`: botón "Create Draft" y "Abrir en Gmail" (link directo al draft) para que el usuario haga `Send` manualmente.
6. **No implementar envío automático (`users.messages.send`) en esta fase.** Si más adelante se quiere, es una acción explícita y separada — requiere confirmación del usuario antes de que `gmail-agent` la implemente.

**Done cuando:** al presionar "Create Draft" aparece el draft real en Gmail con el CV adjunto, listo para revisión y envío manual. `security-agent` debe revisar los scopes de OAuth (least privilege) y el manejo de `token.json` antes de cerrar esta fase.

---

## Fase 8 — Estado `SENT` y trazabilidad

**Agente:** `backend-engineer` (use case + endpoint) + `frontend-agent` (vista de histórico).

1. Como el envío final es manual en Gmail, el sistema no puede saber automáticamente cuándo se envió:
   * Opción simple (recomendada para el MVP): botón "Mark as Sent" → `POST /api/applications/{id}/mark-sent` actualiza `status=SENT` y `sent_at=now()`.
   * Opción avanzada (fase posterior, no MVP): usar `users.drafts.get`/`users.messages.list` para detectar que el draft ya no existe (fue enviado) — la implementaría `gmail-agent`.
2. `frontend-agent`: vista de histórico/tablero — jobs por estado, aplicaciones enviadas por semana, por categoría.

**Done cuando:** se puede ver de un vistazo cuántas ofertas están en cada estado y cuántas aplicaciones se han enviado.

---

## Fase 9 — Scheduler

**Agente:** `backend-engineer` — el scheduler es un punto de entrada más (como la API), driven por tiempo en vez de HTTP, así que vive junto a `app/presentation/**` bajo su ownership.

1. `app/presentation/scheduler/jobs.py` con APScheduler: job diario (ej. 08:00) que invoca en secuencia los use cases ya existentes (`CollectFeedPosts → AnalyzeJobPost → SelectCV → GenerateApplicationEmail`), dejando todo en `EMAIL_GENERATED` listo para revisión humana. **Nunca** crea drafts ni envía automáticamente sin pasar por Fase 6/7 manualmente, salvo que el usuario decida lo contrario explícitamente.
2. Logging estructurado de cada corrida (posts nuevos, relevantes, errores) — sin secretos.
3. Notificación simple opcional (log a archivo, o email a uno mismo) resumiendo la corrida.

**Done cuando:** dejar la app corriendo produce, sin intervención, una bandeja de jobs `EMAIL_GENERATED` lista para revisar al día siguiente — reutilizando los mismos use cases de las fases 2-5, no lógica duplicada.

---

## Fase 10 — Tests, Docker y CI/CD

**Agente:** `testing-agent` (cobertura) + `orchestrator` (Docker/CI — tooling de repo, no pertenece a ninguna capa de arquitectura, así que no tiene un agente de capa dedicado).

1. `testing-agent`: cerrar huecos de cobertura — dedup, analyzer (mockeado), matcher, email generator (mockeado), endpoints principales con `httpx`/`TestClient`, y al menos un test de integración real contra PostgreSQL.
2. `orchestrator`: `Dockerfile` para la app + `docker-compose.yml` completo (app + postgres); GitHub Actions (`.github/workflows/ci.yml`) que en cada push corra `ruff`, `mypy`, `pytest` y build de la imagen Docker.
3. `README.md` con instrucciones reales de instalación, variables de entorno necesarias y cómo correr cada fase.

**Done cuando:** `docker-compose up` levanta todo el sistema desde cero en una máquina limpia, y el CI pasa en verde.

---

## Orden recomendado de ejecución (resumen)

```
Fase 0  Setup                        → architect → orchestrator
Fase 1  DB + modelos                 → domain-engineer → database-agent
Fase 2  LinkedIn Collector + dedup   → linkedin-agent
Fase 3  Job Analyzer (LLM)           → llm-agent
Fase 4  CV Matcher                   → cv-matching-agent
Fase 5  Email Generator (LLM)        → llm-agent
Fase 6  API + Dashboard              → backend-engineer + frontend-agent
Fase 7  Gmail Drafts + adjunto CV    → gmail-agent
Fase 8  Estado SENT + trazabilidad   → backend-engineer + frontend-agent
Fase 9  Scheduler                    → backend-engineer
Fase 10 Tests + Docker + CI/CD       → testing-agent + orchestrator
```

Transversales en cada fase: `testing-agent`, `code-reviewer` (obligatorio al terminar cada agente), `security-agent` (especialmente Fases 2 y 7), `token-optimization-agent` (desde Fase 3).

Cada fase es usable de forma standalone antes de continuar — esto permite validar con datos reales de tu propio LinkedIn en cada paso en vez de construir todo a ciegas.
