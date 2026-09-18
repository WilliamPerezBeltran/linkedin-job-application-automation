# Engineering Standards — Job Application Automation

Quiero desarrollar esta aplicación siguiendo estándares profesionales de ingeniería de software.

El objetivo NO es solamente que funcione. Debe ser:

* mantenible;
* testeable;
* extensible;
* segura;
* desacoplada;
* observable;
* fácil de entender;
* preparada para crecer sin convertirse en un monolito difícil de mantener.

Utilizar obligatoriamente:

* Clean Architecture;
* SOLID;
* DRY;
* KISS;
* YAGNI;
* separación de responsabilidades;
* dependency inversion;
* programación orientada a interfaces/protocolos cuando aporte valor;
* composición sobre herencia;
* typed code;
* configuración externa;
* dependency injection;
* automated testing;
* structured logging;
* manejo explícito de errores.

No aplicar patrones de diseño innecesariamente.

La simplicidad tiene prioridad sobre la sobreingeniería.

---

# 1. Clean Architecture

La arquitectura debe separar claramente:

```text
Domain
Application
Infrastructure
Interface / Presentation
```

Propuesta:

```text
app/
│
├── domain/
│   ├── entities/
│   ├── value_objects/
│   ├── repositories/
│   ├── services/
│   └── exceptions/
│
├── application/
│   ├── use_cases/
│   ├── dto/
│   ├── commands/
│   └── services/
│
├── infrastructure/
│   ├── database/
│   ├── linkedin/
│   ├── gmail/
│   ├── llm/
│   ├── cv/
│   └── config/
│
├── presentation/
│   ├── api/
│   └── web/
│
└── main.py
```

La regla fundamental:

```text
Infrastructure → Application → Domain
Presentation  → Application → Domain
```

El Domain NO debe depender de:

* FastAPI;
* SQLAlchemy;
* Playwright;
* Gmail;
* OpenAI;
* Anthropic;
* PostgreSQL;
* filesystem;
* HTTP clients.

El dominio debe ser independiente de frameworks.

---

# 2. Dependency Rule

Las dependencias siempre deben apuntar hacia el interior.

```text
┌─────────────────────────────────────┐
│           Infrastructure            │
│                                     │
│ Playwright / PostgreSQL / Gmail     │
│ OpenAI / Anthropic / Filesystem     │
└──────────────────┬──────────────────┘
                   │
                   ▼
┌─────────────────────────────────────┐
│            Application              │
│                                     │
│ Use Cases / DTOs / Orchestration    │
└──────────────────┬──────────────────┘
                   │
                   ▼
┌─────────────────────────────────────┐
│               Domain                │
│                                     │
│ Entities / Rules / Interfaces      │
└─────────────────────────────────────┘
```

El Domain nunca debe importar Infrastructure.

Ejemplo incorrecto:

```python
from sqlalchemy import Column
```

dentro de una entidad de dominio.

Ejemplo correcto:

```python
class Job: ...
```

y la persistencia se implementa fuera del dominio.

---

# 3. SOLID

## S — Single Responsibility

Cada clase debe tener una única responsabilidad.

Incorrecto:

```python
LinkedInService
```

que:

* abre navegador;
* hace scraping;
* guarda PostgreSQL;
* llama al LLM;
* selecciona CV;
* genera email;
* crea Gmail draft.

Esto está prohibido.

Separar:

```text
LinkedInBrowser
LinkedInFeedCollector
PostParser
PostRepository
JobClassifier
CVMatcher
EmailGenerator
GmailDraftService
```

---

## O — Open/Closed Principle

El sistema debe permitir agregar proveedores sin modificar el core.

Por ejemplo:

```python
class LLMProvider(Protocol):
    def analyze_job(...):
        ...

    def generate_email(...):
        ...
```

Implementaciones:

```text
OpenAIProvider
AnthropicProvider
```

Agregar otro proveedor no debería requerir modificar los casos de uso.

---

## L — Liskov Substitution

Las implementaciones de una interfaz deben poder sustituirse sin romper el comportamiento esperado.

Ejemplo:

```text
OpenAIProvider
AnthropicProvider
```

deben cumplir exactamente el contrato de:

```text
LLMProvider
```

---

## I — Interface Segregation

No crear interfaces gigantes.

Incorrecto:

```python
class JobAutomationService:
    scrape()
    analyze()
    match_cv()
    generate_email()
    send_email()
```

Preferir interfaces pequeñas:

```text
FeedCollector
JobAnalyzer
CVMatcher
EmailGenerator
EmailDraftRepository
```

---

## D — Dependency Inversion

Los use cases deben depender de abstracciones.

Ejemplo:

```python
class AnalyzeJobUseCase:
    def __init__(self, analyzer: JobAnalyzer, repository: JobRepository): ...
```

No:

```python
self.openai = OpenAI(...)
```

dentro del use case.

---

# 4. Domain Layer

El dominio debe contener reglas de negocio.

Ejemplos:

```text
Job
CVProfile
Application
EmailDraft
Skill
```

Value Objects:

```text
EmailAddress
JobId
CVId
ApplicationId
```

Las validaciones de negocio deben estar aquí cuando corresponda.

Ejemplo:

```python
EmailAddress
```

debe garantizar que el email sea válido.

No depender de Gmail para validar el dominio.

---

# 5. Application Layer

Los casos de uso deben representar acciones reales del sistema.

Ejemplos:

```text
CollectFeedPosts
AnalyzeJobPost
SelectBestCV
GenerateApplicationEmail
CreateGmailDraft
ReviewApplication
IgnoreJob
```

Un use case debe coordinar dependencias.

No debe conocer detalles de:

* SQLAlchemy;
* Playwright;
* HTTP;
* Gmail SDK;
* OpenAI SDK.

---

# 6. Infrastructure Layer

Aquí deben vivir las implementaciones concretas.

```text
infrastructure/
│
├── linkedin/
│   ├── playwright_browser.py
│   └── linkedin_feed_collector.py
│
├── database/
│   ├── sqlalchemy_models.py
│   ├── repositories/
│   └── session.py
│
├── llm/
│   ├── openai_provider.py
│   └── anthropic_provider.py
│
├── gmail/
│   ├── gmail_client.py
│   └── gmail_draft_repository.py
│
└── cv/
    └── filesystem_cv_repository.py
```

Estas implementaciones cumplen las interfaces definidas por Application/Domain.

---

# 7. Dependency Injection

Utilizar Dependency Injection.

FastAPI puede utilizarse para inyectar dependencias en Presentation.

Los use cases no deben crear sus propias dependencias.

Incorrecto:

```python
class GenerateEmail:
    def __init__(self):
        self.client = OpenAI()
```

Correcto:

```python
class GenerateEmail:
    def __init__(self, llm: LLMProvider):
        self.llm = llm
```

La composición de dependencias debe realizarse en un punto central.

---

# 8. DTOs

No exponer directamente entidades de dominio en la API.

Utilizar DTOs:

```text
JobResponse
ApplicationResponse
GenerateEmailRequest
CreateDraftRequest
CVResponse
```

Pydantic para validación en los boundaries.

---

# 9. Repository Pattern

El dominio/application debe depender de interfaces.

Ejemplo:

```python
class JobRepository(Protocol):
    def save(self, job: Job) -> Job: ...

    def find_by_hash(self, content_hash: str) -> Job | None: ...

    def find_pending(self) -> list[Job]: ...
```

La implementación:

```text
SQLAlchemyJobRepository
```

está en Infrastructure.

Esto permite probar los use cases sin PostgreSQL.

---

# 10. Error Handling

No utilizar:

```python
except Exception:
    pass
```

Nunca ocultar errores.

Crear excepciones específicas:

```text
LinkedInAuthenticationError
LinkedInNavigationError
FeedScrapingError
JobAnalysisError
CVNotFoundError
EmailGenerationError
GmailAuthenticationError
GmailDraftError
```

Separar:

```text
Domain errors
Application errors
Infrastructure errors
```

Los errores deben transformarse correctamente en HTTP responses en Presentation.

---

# 11. Logging

Utilizar logging estructurado.

Nunca:

```python
print(...)
```

en producción.

Registrar:

```text
request_id
job_id
application_id
operation
duration
status
error_type
```

Nunca registrar:

```text
password
API key
OAuth token
access token
refresh token
```

---

# 12. Configuration

Toda configuración debe estar fuera del código.

Utilizar:

```text
.env
.env.example
```

y una clase de configuración tipada.

Ejemplo:

```python
class Settings:
    database_url: str
    llm_provider: str
    google_client_id: str
    google_client_secret: str
```

No utilizar:

```python
OPENAI_API_KEY = "..."
```

en el código.

---

# 13. Security

Aplicar:

* least privilege;
* secrets management;
* OAuth 2.0;
* token encryption cuando sea necesario;
* input validation;
* output validation;
* safe file handling;
* path traversal protection;
* secure logging.

No almacenar contraseñas de LinkedIn.

No almacenar API keys en la base de datos.

Validar todos los paths de CV.

---

# 14. LinkedIn Boundary

LinkedIn debe ser tratado como un sistema externo no confiable.

Crear una interfaz (firma fijada en ADR-003, `docs/decisions/003-feed-collector-interface.md`):

```python
class FeedCollector(Protocol):
    def collect(self) -> list[RawFeedPost]: ...
```

`RawFeedPost` es un DTO en `app/application/dto/raw_feed_post.py` — `linkedin-agent` nunca construye ni devuelve `Job` directamente (ver ADR-003).

Playwright será solamente una implementación.

No permitir que Playwright se propague hacia Domain/Application.

Además, el collector debe tener una política estricta:

```text
ALLOWED:
LinkedIn feed

NOT ALLOWED:
profiles
companies
jobs
external URLs
messaging
likes
comments
connections
applications
```

No utilizar selectores dispersos por toda la aplicación.

Centralizar los selectores del sitio.

---

# 15. LLM Boundary

El LLM también debe considerarse una dependencia externa.

Nunca permitir que una respuesta del LLM entre directamente al dominio sin validación.

Pipeline:

```text
LLM
 ↓
Raw response
 ↓
JSON parser
 ↓
Pydantic validation
 ↓
Application DTO
 ↓
Domain
```

Utilizar structured outputs cuando estén disponibles.

Validar:

* tipos;
* enums;
* confidence;
* emails;
* skills;
* job category.

No confiar ciegamente en el LLM.

---

# 16. Prompt Versioning

Los prompts deben estar versionados.

Por ejemplo:

```text
prompts/
├── job-analysis/
│   └── v1.txt
├── cv-matching/
│   └── v1.txt
└── email-generation/
    └── v1.txt
```

Guardar en la base de datos:

```text
prompt_version
model
timestamp
```

Esto permite reproducir por qué se generó determinado resultado.

---

# 17. CV Matching

No utilizar exclusivamente un LLM.

Crear un sistema híbrido:

```text
Job requirements
        ↓
Skill normalization
        ↓
Deterministic matching
        ↓
Optional LLM semantic analysis
        ↓
Final recommendation
```

El sistema debe poder explicar:

```text
Matched:
Java
Spring Boot
Kafka

Missing:
AWS

Recommended CV:
Java CV
```

Nunca inventar skills presentes en el CV.

---

# 18. Idempotency

Las operaciones críticas deben ser idempotentes.

Ejemplo:

```text
same LinkedIn post
        ↓
same content hash
        ↓
DO NOT create duplicate job
```

Crear índices únicos en PostgreSQL.

Para Gmail:

```text
same application
        ↓
DO NOT create multiple drafts accidentally
```

---

# 19. Database Transactions

Definir límites transaccionales claros.

Ejemplo:

```text
Save Job
+
Save Analysis
+
Save CV Match
```

debe ser consistente.

No mantener transacciones abiertas durante:

* llamadas al LLM;
* scraping;
* llamadas a Gmail.

Las operaciones externas deben ocurrir fuera de transacciones largas.

---

# 20. Testing Pyramid

Priorizar:

```text
             E2E
            /   \
       Integration
          /     \
       Unit Tests
```

La mayoría deben ser unit tests.

Probar especialmente:

```text
Domain
Use Cases
CV Matcher
Email extraction
Job classification parsing
Deduplication
State transitions
```

Los tests no deben depender de Internet.

---

# 21. External Services

Para tests:

```text
FakeFeedCollector
FakeLLMProvider
FakeCVRepository
FakeGmailProvider
InMemoryJobRepository
```

No llamar a:

```text
LinkedIn
OpenAI
Anthropic
Gmail
```

en unit tests.

---

# 22. State Machine

No utilizar strings arbitrarios repartidos por el código.

Crear un enum:

```python
class ApplicationStatus(Enum):
    SCRAPED = "scraped"
    ANALYZED = "analyzed"
    CV_SELECTED = "cv_selected"
    EMAIL_GENERATED = "email_generated"
    DRAFT_CREATED = "draft_created"
    SENT = "sent"
    IGNORED = "ignored"
    ERROR = "error"
```

Definir explícitamente las transiciones válidas.

---

# 23. API Design

Utilizar REST.

Ejemplos:

```text
GET    /api/jobs
GET    /api/jobs/{id}
POST   /api/jobs/{id}/analyze
POST   /api/jobs/{id}/generate-email
POST   /api/jobs/{id}/create-draft
POST   /api/jobs/{id}/ignore

GET    /api/cvs
GET    /api/applications
GET    /api/applications/{id}
```

Utilizar:

* HTTP status codes correctos;
* request validation;
* response DTOs;
* pagination;
* filtering;
* consistent error responses.

---

# 24. Pagination

Nunca devolver cantidades ilimitadas.

Ejemplo:

```text
GET /api/jobs?page=1&page_size=20
```

Definir límites máximos.

---

# 25. Frontend

El frontend no debe contener reglas de negocio.

Debe comunicarse con la API.

No duplicar:

```text
CV matching
job classification
application state
```

en React.

---

# 26. Repository Structure

Propuesta:

```text
job-application-automation/
│
├── app/
│   ├── domain/
│   ├── application/
│   ├── infrastructure/
│   └── presentation/
│
├── tests/
│   ├── unit/
│   ├── integration/
│   └── e2e/
│
├── prompts/
│   ├── job-analysis/
│   │   └── v1.txt
│   ├── cv-matching/
│   │   └── v1.txt
│   └── email-generation/
│       └── v1.txt
│
├── cvs/
│
├── migrations/
│
├── docs/
│   ├── architecture/
│   ├── decisions/
│   ├── agents/
│   │   └── AGENTS.md
│   ├── optimization/
│   │   ├── token-budget.md
│   │   └── llm-cost-analysis.md
│   └── security.md
│
├── .agents/           ← NO usar esta carpeta; los agentes de Claude Code viven en .claude/agents/
├── .env.example
├── .gitignore
├── pyproject.toml
├── docker-compose.yml
└── README.md
```

Separación de responsabilidades entre carpetas relacionadas con agentes/LLM:

```
.claude/agents/      → instrucciones que consume el runtime de Claude Code para cada agente
prompts/              → prompts reales que usa la aplicación en producción, versionados por feature
docs/optimization/    → métricas, decisiones y resultados de optimización de tokens/costo
```

---

# 27. Architecture Decision Records

Para decisiones arquitectónicas importantes crear ADRs:

```text
docs/decisions/
├── 001-monolith.md
├── 002-postgresql.md
├── 003-playwright.md
├── 004-gmail-api.md
└── 005-llm-provider-abstraction.md
```

Cada ADR debe explicar:

```text
Context
Decision
Alternatives
Consequences
```

---

# 28. Code Quality

Configurar:

```text
ruff
mypy
pytest
pre-commit
```

Opcionalmente:

```text
bandit
pip-audit
```

CI debe ejecutar:

```text
lint
type checking
unit tests
integration tests
security checks
```

No permitir merge si fallan los checks.

---

# 29. Type Safety

Utilizar type hints en todo el código.

Evitar:

```python
def process(data):
```

Preferir:

```python
def process(job: Job) -> JobAnalysis:
```

Evitar `Any` salvo que exista una razón documentada.

---

# 30. Documentation

README debe explicar:

```text
What it does
Architecture
Installation
Configuration
Database setup
Google OAuth setup
LinkedIn session setup
Running locally
Testing
Environment variables
Security
Known limitations
```

También documentar decisiones importantes.

---

# 31. No Overengineering

No introducir una tecnología solamente porque sea popular.

Antes de agregar una dependencia responder:

1. ¿Qué problema resuelve?
2. ¿Es realmente necesario?
3. ¿Existe una solución estándar más sencilla?
4. ¿Cuál es el coste de mantenimiento?
5. ¿Cómo se probará?

Si no existe una justificación clara, NO agregarla.

---

# 32. Development Workflow

Antes de implementar cualquier feature:

```text
1. Understand requirement
2. Identify affected layer
3. Identify dependencies
4. Design interfaces
5. Implement domain rules
6. Implement use case
7. Implement infrastructure adapter
8. Add tests
9. Run lint
10. Run type checking
11. Run tests
12. Review architecture
```

No mezclar múltiples responsabilidades en un solo commit.

---

# 33. Refactoring Rule

Si encuentras:

* código duplicado;
* clases gigantes;
* funciones de más de una responsabilidad;
* imports circulares;
* infraestructura dentro del dominio;
* lógica de negocio dentro de controllers;
* lógica de negocio dentro de repositories;

detenerse y corregirlo antes de continuar.

---

# 34. Anti-patterns prohibidos

No utilizar:

```text
God Objects
God Services
God Controllers
Global mutable state
Singletons innecesarios
Service Locator
Circular dependencies
Massive functions
Magic strings
Hardcoded secrets
Business logic in controllers
Business logic in SQL repositories
Framework dependencies in domain
Silent exception handling
Copy/paste implementations
Premature abstraction
Premature microservices
```

---

# 35. Principle of Least Knowledge

Los componentes deben conocer solamente lo que necesitan.

Evitar cadenas como:

```python
job.application.cv.profile.skills...
```

Crear métodos apropiados en las entidades o servicios.

---

# 36. Domain Events

NO implementar event-driven architecture inicialmente.

Solo introducir Domain Events si existe una necesidad real.

No utilizar Kafka/Redis simplemente para demostrar arquitectura avanzada.

---

# 37. Performance

Optimizar solamente después de medir.

Medir:

```text
scraping duration
LLM latency
LLM token usage
database queries
API latency
Gmail latency
```

No realizar optimizaciones prematuras.

---

# 38. Final Architecture Goal

La arquitectura final debe permitir cambiar:

```text
LinkedIn collector
PostgreSQL
OpenAI
Anthropic
Gmail
CV storage
Frontend
```

sin modificar las reglas principales del dominio.

El core debe permanecer independiente.

---

# 39. Critical Requirement

Antes de escribir código, analiza el proyecto existente.

Entrega primero:

```text
1. Architecture assessment
2. Current problems
3. SOLID violations
4. Clean Architecture violations
5. Security risks
6. Technical debt
7. Proposed architecture
8. Folder structure
9. Dependency graph
10. Implementation plan
```

NO comiences implementando inmediatamente.

Primero presenta el análisis.

Después de mi aprobación, implementa por etapas.

No hagas commit ni push automáticamente.

No inventes requisitos.

Si una decisión arquitectónica tiene más de una alternativa razonable, explícame las alternativas y sus trade-offs antes de decidir.

Prioridad:

```text
Correctness
Security
Maintainability
Testability
Simplicity
Observability
Performance
```

en ese orden.
