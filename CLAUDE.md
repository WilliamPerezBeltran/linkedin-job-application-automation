# linkedin-job-application-automation

## Objetivo del proyecto

Aplicación local (no microservicios) que automatiza parcialmente la búsqueda y postulación a empleos vía LinkedIn:

1. Login con credenciales del usuario.
2. Scraping **solo del feed/index** de LinkedIn (nada de entrar a links, perfiles u otras páginas — únicamente lo que aparece en el feed, como si el usuario estuviera scrolleando).
3. Detección de posts que sean ofertas de empleo relevantes (dev, IA, ML, deep learning).
4. Extracción de datos de la oferta (empresa, cargo, tecnologías, email, descripción).
5. Clasificación de la oferta por categoría (Java, Python, AI/ML, Deep Learning, JavaScript/Node, Go, Elixir, Full Stack).
6. Selección automática del CV correspondiente desde una carpeta local con CVs predisñados por categoría.
7. Generación de email de postulación (subject + body) vía LLM (OpenAI/Anthropic), reemplazando el flujo manual actual de copiar/pegar en ChatGPT.
8. Creación de **draft en Gmail** (no envío directo) con el CV adjunto, **con paso de revisión/aprobación manual antes de enviar** (nunca envío automático sin confirmación).

Nota legal/ética: LinkedIn puede limitar o prohibir ciertos usos automatizados de su plataforma — revisar sus términos y evitar mecanismos de evasión de controles, CAPTCHAs o detección de automatización.

Roadmap detallado paso a paso: ver [`ROADMAP.md`](./ROADMAP.md).

Optimización de tokens/costo en las llamadas al LLM (Analyzer, Email Generator): ver [`TOKEN_OPTIMIZATION.md`](./TOKEN_OPTIMIZATION.md).

Estándares de ingeniería obligatorios (Clean Architecture, SOLID, testing, seguridad, etc.): ver [`ENGINEERING_STANDARDS.md`](./ENGINEERING_STANDARDS.md). **Nota:** esa guía define una estructura de carpetas por capas (`domain/application/infrastructure/presentation`) que reemplaza la estructura plana (`app/linkedin/`, `app/jobs/`, etc.) descrita más arriba en este archivo — al implementar, seguir la estructura por capas de `ENGINEERING_STANDARDS.md`.

Arquitectura de agentes especializados (Orchestrator, Architect, Domain, Backend, LinkedIn, LLM, CV Matching, Gmail, Database, Frontend, Security, Testing, Code Review — ownership, principios y reglas de coordinación entre ellos): ver [`docs/agents/AGENTS.md`](./docs/agents/AGENTS.md).

## Decisión arquitectónica clave

**No** usar microservicios, Kubernetes, Kafka, ni AWS en el MVP. Todo corre localmente (Docker opcional para levantar Postgres) hasta que exista una necesidad real de escalar. Celery + Redis solo se consideran si más adelante hace falta procesamiento asíncrono pesado; para el scheduler inicial basta APScheduler o cron.

La automatización llega hasta "oferta detectada → CV seleccionado → correo generado → **draft creado en Gmail**". El envío final (`Send`) queda bajo aprobación humana — esto facilita depurar errores y evita postulaciones incorrectas enviadas por error.

Regla de diseño: no un único agente de IA controlando todo el flujo. Cada componente (Collector, Detector/Classifier, CV Matcher, Email Generator) tiene una sola responsabilidad.

## Stack

| Componente | Tecnología |
|---|---|
| Backend | Python + FastAPI |
| Automatización navegador | Playwright |
| Frontend | React (o HTML simple al inicio) |
| Base de datos | PostgreSQL |
| ORM | SQLAlchemy |
| IA (clasificación + generación de email) | OpenAI / Anthropic |
| Extracción de texto | BeautifulSoup/lxml donde aplique |
| CVs | Archivos PDF locales, organizados por carpeta/categoría + `config/cvs.yaml` |
| Correo | Gmail API + OAuth 2.0 (crear drafts, no Playwright sobre gmail.com) |
| Scheduler | APScheduler (o cron); Celery+Redis solo si se vuelve necesario |
| Configuración | `.env` + YAML |
| Contenedor | Docker / docker-compose |
| Tests | Pytest |
| CI/CD | GitHub Actions |

## Pipeline / flujo de datos

```
LinkedIn Feed
   │ (Playwright, solo feed, sin navegar a links/perfiles)
   ▼
Job Collector → dedup por hash → guarda en PostgreSQL (jobs, status=SCRAPED)
   │
   ▼
Job Analyzer (LLM) → is_job, category, seniority, skills, email
   │                     status=ANALYZED / NOT_RELEVANT
   ▼
CV Matcher (según skills/categoría + config/cvs.yaml)
   │                     status=CV_SELECTED
   ▼
Email Generator (LLM → subject + body en JSON)
   │                     status=EMAIL_GENERATED
   ▼
Application Review Dashboard (preview, edición manual)
   │
   ▼
Gmail API → crea Draft con CV adjunto     status=DRAFT_CREATED
   │
   ▼
Usuario revisa y hace Send manualmente     status=SENT
```

## Máquina de estados

```
SCRAPED → ANALYZED → RELEVANT → CV_SELECTED → EMAIL_GENERATED → DRAFT_CREATED → SENT
                └──→ NOT_RELEVANT
```

Evita reprocesar la misma oferta más de una vez.

## Deduplicación

LinkedIn puede repetir el mismo post varias veces en el feed. Se genera un hash (`SHA256` del contenido normalizado del post) y se usa como clave única antes de insertar en `jobs`.

## Estructura de carpetas propuesta

```
job-application-automation/
│
├── app/
│   ├── api/
│   │   └── routes/
│   │
│   ├── linkedin/
│   │   ├── browser.py
│   │   ├── scraper.py
│   │   └── parser.py
│   │
│   ├── jobs/
│   │   ├── detector.py
│   │   ├── analyzer.py
│   │   └── repository.py
│   │
│   ├── cv/
│   │   ├── matcher.py
│   │   └── repository.py
│   │
│   ├── ai/
│   │   ├── client.py
│   │   └── email_generator.py
│   │
│   ├── gmail/
│   │   ├── auth.py
│   │   └── drafts.py
│   │
│   ├── database/
│   │   ├── models.py
│   │   └── database.py
│   │
│   ├── scheduler/
│   │   └── jobs.py
│   │
│   └── main.py
│
├── cvs/
│   ├── java/
│   ├── python/
│   ├── ai/
│   ├── frontend/
│   ├── go/
│   └── elixir/
│
├── frontend/
│
├── tests/
│
├── config/
│   └── cvs.yaml
│
├── docker-compose.yml
├── .env.example
├── requirements.txt
└── README.md
```

## Modelo de datos (PostgreSQL)

**jobs**: `id, source, author, content, content_hash (unique), email, url, published_at, scraped_at, status, created_at`

**job_analysis**: `job_id, job_type, seniority, skills, languages, frameworks, cloud, ai_related, match_score, recommended_cv, generated_email, subject`

**applications**: `id, job_id, email, subject, body, cv_path, gmail_draft_id, status, sent_at`

## Configuración de CVs (`config/cvs.yaml`)

```yaml
cvs:
  java:
    file: cvs/java/william-java.pdf
    skills: [Java, Spring Boot, Kafka, Microservices]
  python:
    file: cvs/python/william-python.pdf
    skills: [Python, FastAPI, Django]
  ai:
    file: cvs/ai/william-ai.pdf
    skills: [Python, LLM, RAG, Machine Learning]
  frontend:
    file: cvs/frontend/william-fullstack.pdf
    skills: [JavaScript, Node.js, React]
  go:
    file: cvs/golang/william-go.pdf
    skills: [Go, Golang]
  elixir:
    file: cvs/elixir/william-elixir.pdf
    skills: [Elixir, Phoenix]
```

El matching CV↔oferta se basa en el cruce de `skills` detectadas por el LLM contra `skills` de cada CV en este archivo, no solo en el nombre de la categoría.

## Nombre del repositorio

Nombre elegido: `linkedin-job-application-automation` (ya usado como nombre de esta carpeta/repo).

Descripción sugerida para GitHub:
> Automated workflow for collecting LinkedIn job posts, analyzing opportunities with AI, generating application emails, and selecting the appropriate CV.

Alternativas consideradas: `linkedin-job-scraper`, `linkedin-job-automation`, `job-application-automation`, `ai-job-application-assistant`, `linkedin-career-automation`, `job-hunter-ai`, `linkedin-job-assistant`, `automated-job-applications`.
