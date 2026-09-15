# Roadmap — linkedin-job-application-automation

Cada fase tiene un entregable verificable ("Done cuando..."). No pasar a la siguiente fase sin poder demostrar la anterior funcionando. Ver arquitectura completa en [`CLAUDE.md`](./CLAUDE.md).

---

## Fase 0 — Setup del proyecto

1. Crear estructura de carpetas base (`app/`, `cvs/`, `tests/`, `config/`).
2. Inicializar `pyproject.toml` o `requirements.txt` con: `fastapi`, `uvicorn`, `sqlalchemy`, `psycopg2-binary` (o `asyncpg`), `alembic`, `playwright`, `pydantic`, `pydantic-settings`, `python-dotenv`, `pyyaml`, `pytest`, `httpx`.
3. Crear `.env.example` con variables necesarias (ver Fase 1) y `.env` real (gitignored).
4. Crear `.gitignore` (incluir `.env`, `data/`, `__pycache__`, `*.pdf` si los CVs son sensibles, `credentials.json`, `token.json`).
5. `docker-compose.yml` con un servicio `postgres` (para no instalar Postgres localmente).
6. Playwright: `playwright install chromium`.

**Done cuando:** `docker-compose up -d postgres` levanta la base y `python -c "import fastapi, playwright, sqlalchemy"` no falla.

---

## Fase 1 — Base de datos y modelos

1. Definir `app/database/database.py` (engine SQLAlchemy + sesión).
2. Definir `app/database/models.py` con las tablas: `jobs`, `job_analysis`, `applications` (ver esquema en `CLAUDE.md`). Incluir `content_hash` único en `jobs` para deduplicación desde el inicio.
3. Configurar Alembic para migraciones (`alembic init`, primera migración con las 3 tablas).
4. Variables en `.env`: `DATABASE_URL=postgresql://user:pass@localhost:5432/jobs_db`.
5. Escribir un test simple (`tests/test_database.py`) que abra sesión, inserte un `job` de prueba y lo lea.

**Done cuando:** `alembic upgrade head` crea las tablas y el test de inserción/lectura pasa.

---

## Fase 2 — LinkedIn Collector (solo feed)

1. `app/linkedin/browser.py`: función que lanza Playwright (Chromium, `headless=False` al principio para depurar), reutilizando una sesión guardada (`storage_state`) para no tener que hacer login cada vez.
2. `app/linkedin/login.py`: login con usuario/contraseña desde `.env` (`LINKEDIN_EMAIL`, `LINKEDIN_PASSWORD`), guardando `storage_state.json` tras el primer login exitoso. Manejar el caso de verificación en dos pasos (pausa manual la primera vez).
3. `app/linkedin/feed_scraper.py`: navegar a `linkedin.com/feed/`, hacer scroll controlado (N veces, con límite configurable), y extraer de cada post visible: autor, texto, timestamp relativo, URL del post si existe. **Regla explícita: nunca hacer `click` en un post ni navegar fuera de `/feed/`.**
4. `app/linkedin/parser.py`: normalizar el HTML/texto extraído a un dict limpio.
5. Calcular `content_hash = sha256(normalized_content)` y hacer upsert en `jobs` (si el hash ya existe, ignorar).
6. Guardar cada post como `status=SCRAPED`.
7. Exponer un comando manual (`python -m app.linkedin.feed_scraper` o endpoint `POST /scrape`) para correrlo a demanda mientras se desarrolla.

**Done cuando:** correr el scraper una vez guarda N posts nuevos en `jobs`; correrlo de nuevo inmediatamente después no duplica filas (dedup funcionando).

⚠️ Antes de automatizar el login, revisar los Términos de Servicio de LinkedIn — este proyecto es para uso personal, sin scraping masivo ni evasión de controles anti-bot.

---

## Fase 3 — Job Analyzer (LLM)

1. `app/ai/client.py`: wrapper simple sobre la API de OpenAI/Anthropic (API key desde `.env`).
2. `app/jobs/analyzer.py`: función que recibe el `content` de un job y devuelve JSON estructurado (usar structured output / JSON mode):
   ```json
   {"is_job": true, "category": "software_engineering", "seniority": "senior",
    "skills": ["Java","Spring Boot","Kafka","AWS"], "email": "recruiter@example.com"}
   ```
3. Prompt con lista cerrada de categorías (Java, Python, AI/ML, Deep Learning, JavaScript/Node, Go, Elixir, Full Stack, Other).
4. Guardar el resultado en `job_analysis` y actualizar `jobs.status` a `ANALYZED` o `NOT_RELEVANT` (si `is_job=false`).
5. Job runner que procese todos los `jobs` en estado `SCRAPED` en batch.
6. Tests con 4-5 posts de ejemplo (reales, anonimizados) fijos como fixtures, mockeando la llamada al LLM.

**Done cuando:** correr el analyzer sobre los posts scrapeados en Fase 2 llena `job_analysis` correctamente y mueve el `status` de cada job.

---

## Fase 4 — CV Matcher

1. Crear `config/cvs.yaml` con el mapeo categoría→archivo→skills (ver ejemplo en `CLAUDE.md`).
2. Colocar los PDFs reales en `cvs/<categoria>/`.
3. `app/cv/repository.py`: cargar y parsear `cvs.yaml`.
4. `app/cv/matcher.py`: dado el `skills` detectado por el analyzer, calcular el CV con mayor intersección de skills (score simple de overlap; empieza con algo tan simple como `len(set(job_skills) & set(cv_skills))`).
5. Guardar `recommended_cv` en `job_analysis` y mover `status` a `CV_SELECTED`.
6. Test: dado un job con skills `["Java","Spring Boot"]`, verificar que selecciona el CV de Java y no el de Python.

**Done cuando:** cada job en `ANALYZED`/`RELEVANT` termina con un `recommended_cv` sensato.

---

## Fase 5 — Email Generator

1. `app/ai/email_generator.py`: recibe `job_analysis` + texto del CV seleccionado (o un resumen fijo del CV, no hace falta parsear el PDF completo al inicio) y pide al LLM un JSON `{"subject": "...", "body": "..."}`.
2. Guardar `subject`/`generated_email` en `job_analysis`, mover `status` a `EMAIL_GENERATED`.
3. Test con fixture, mockeando el LLM, verificando que el JSON parseado tiene las claves esperadas.

**Done cuando:** para un job en `CV_SELECTED`, se genera subject+body coherente y queda persistido.

---

## Fase 6 — API + Dashboard (revisión manual)

1. `app/api/routes/jobs.py`: endpoints `GET /jobs` (listado con filtro por status), `GET /jobs/{id}`, `POST /jobs/{id}/ignore` (marca `NOT_RELEVANT` manualmente).
2. `POST /scrape`, `POST /jobs/{id}/analyze`, `POST /jobs/{id}/generate-email` — para disparar cada etapa manualmente desde la UI mientras no hay scheduler.
3. Frontend mínimo (puede ser HTML+JS simple primero, React después): lista de jobs nuevos con skills, CV recomendado, botón "Generate Email", vista previa de subject/body editable.
4. Permitir editar manualmente el subject/body generado antes de crear el draft (guardar la edición en `applications`).

**Done cuando:** desde el navegador se puede ver un job, su CV recomendado, generar/editar el email y ver el preview — sin tocar la base de datos a mano.

---

## Fase 7 — Gmail integration (Drafts)

1. Crear credenciales OAuth 2.0 en Google Cloud Console (Gmail API habilitada), descargar `credentials.json`.
2. `app/gmail/auth.py`: flujo OAuth (guarda `token.json` tras primera autorización).
3. `app/gmail/drafts.py`: función que crea un **draft** (no envía) con `to`, `subject`, `body` y el PDF del CV adjunto (base64 MIME multipart).
4. Endpoint `POST /jobs/{id}/create-draft` → llama a `drafts.py`, guarda `gmail_draft_id` en `applications`, mueve `status` a `DRAFT_CREATED`.
5. En el dashboard, botón "Create Draft" y luego "Abrir en Gmail" (link directo al draft) para que el usuario haga `Send` manualmente.
6. **No implementar envío automático (`users.messages.send`) en esta fase.** Si más adelante se quiere, debe ser una acción explícita y separada, nunca la ruta por defecto.

**Done cuando:** al presionar "Create Draft" aparece el draft real en Gmail con el CV adjunto, listo para revisión y envío manual.

---

## Fase 8 — Estado `SENT` y trazabilidad

1. Como el envío final es manual en Gmail, el sistema no puede saber automáticamente cuándo se envió — dos opciones:
   - Opción simple: botón manual "Mark as Sent" en el dashboard que actualiza `status=SENT` y `sent_at=now()`.
   - Opción avanzada (opcional, fase posterior): usar `users.drafts.get`/`users.messages.list` para detectar que el draft ya no existe como draft (fue enviado).
2. Vista de histórico/tablero: jobs por estado, aplicaciones enviadas por semana, por categoría.

**Done cuando:** se puede ver de un vistazo cuántas ofertas están en cada estado y cuántas aplicaciones se han enviado.

---

## Fase 9 — Scheduler

1. `app/scheduler/jobs.py` con APScheduler: job diario (ej. 08:00) que corre `scrape → analyze → match CV → generate email` en secuencia, dejando todo en `EMAIL_GENERATED` listo para revisión humana (nunca crea drafts ni envía automáticamente sin pasar por Fase 6/7 manualmente, salvo que el usuario decida lo contrario explícitamente).
2. Logging claro de cada corrida (cuántos posts nuevos, cuántos relevantes, errores).
3. Notificación simple opcional (email a uno mismo, o log a archivo) resumiendo la corrida.

**Done cuando:** dejar la app corriendo produce, sin intervención, una bandeja de jobs `EMAIL_GENERATED` lista para revisar al día siguiente.

---

## Fase 10 — Tests, Docker y CI/CD

1. Cobertura de tests: dedup, analyzer (mockeado), matcher, email generator (mockeado), endpoints principales con `httpx`/`TestClient`.
2. `Dockerfile` para la app + `docker-compose.yml` completo (app + postgres).
3. GitHub Actions: workflow que en cada push corra `pytest` y build de la imagen Docker.
4. `README.md` con instrucciones reales de instalación, variables de entorno necesarias y cómo correr cada fase.

**Done cuando:** `docker-compose up` levanta todo el sistema desde cero en una máquina limpia, y el CI pasa en verde.

---

## Orden recomendado de trabajo (resumen)

```
Fase 0  Setup
Fase 1  DB + modelos
Fase 2  LinkedIn Collector (feed only) + dedup
Fase 3  Job Analyzer (LLM)
Fase 4  CV Matcher
Fase 5  Email Generator (LLM)
Fase 6  API + Dashboard de revisión
Fase 7  Gmail Drafts + adjunto CV
Fase 8  Estado SENT + trazabilidad
Fase 9  Scheduler (automatiza 2→5, deja todo listo para revisión)
Fase 10 Tests + Docker + CI/CD
```

Cada fase es usable de forma standalone antes de continuar — esto permite validar con datos reales de tu propio LinkedIn en cada paso en vez de construir todo a ciegas.
