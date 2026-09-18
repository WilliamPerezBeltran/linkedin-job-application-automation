# Dockerfile — imagen de la app (API + dashboard + scheduler), Fase 10 del
# ROADMAP ("Tests, Docker y CI/CD"). Ownership: `orchestrator` (tooling de
# repo, sin agente de capa dedicado — ver docs/agents/AGENTS.md sección 18).
#
# Decisión de diseño: single-stage, no multi-stage. El ROADMAP deja la
# puerta abierta a multi-stage "si aporta valor" -- acá no la aporta: no hay
# ninguna extensión C pesada que compilar (`psycopg2-binary`/`lxml` ya traen
# wheels para Linux), y separar un stage de build no reduciría el tamaño
# final de forma significativa una vez que Playwright/Chromium (el
# componente realmente pesado de esta imagen) ya vive en el stage final de
# todas formas. Complicar esto con stages adicionales no aporta valor real a
# un MVP local de un solo usuario (`docs/agents/AGENTS.md` principio 9,
# YAGNI) y solo agregaría superficie de mantenimiento.
#
# Playwright/Chromium se instalan explícitamente (`playwright install
# --with-deps chromium`) en vez de partir de la imagen oficial
# `mcr.microsoft.com/playwright/python` -- esa imagen trae los tres motores
# (Chromium/Firefox/WebKit) cuando este proyecto usa exclusivamente Chromium
# (ver ROADMAP.md Fase 2 / `app/infrastructure/linkedin/browser.py`),
# resultando en una imagen final más chica.
#
# Esta imagen sirve tanto al proceso de API (`uvicorn app.main:app`) como al
# del scheduler (`python -m app.presentation.scheduler.jobs`) -- son el
# mismo código, solo cambia el comando (ver `docker-compose.yml`, servicios
# `app` y `scheduler`, y el docstring de `app/presentation/scheduler/jobs.py`
# sección "Arranque de los dos procesos": nunca deben correr en el mismo
# proceso/contenedor).

FROM python:3.11-slim

WORKDIR /app

# Dependencias de sistema que Chromium (Playwright) necesita para correr
# headless en Debian/Ubuntu.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates wget gnupg \
    && rm -rf /var/lib/apt/lists/*

# Copiar solo `pyproject.toml` primero para que la instalación de
# dependencias quede en su propia capa de Docker, cacheada mientras el
# archivo no cambie, incluso si el código de `app/` cambia después.
COPY pyproject.toml ./

# `setuptools` con `[tool.setuptools.packages.find] include = ["app*"]`
# necesita que el paquete `app` exista para poder instalar el proyecto -- se
# copia el código real antes de instalar (no hay forma de instalar
# dependencias sin el paquete presente sin recurrir a un `requirements.txt`
# paralelo, que este proyecto no mantiene por diseño, ver ADR-001).
COPY app ./app

RUN pip install --no-cache-dir .

# Instala el navegador Chromium + sus dependencias de sistema.
RUN playwright install --with-deps chromium

# Resto del código/config que la app necesita en runtime. `.dockerignore`
# excluye tests/, frontend/, artefactos de git y de entornos virtuales
# locales -- nunca se copian a la imagen.
COPY migrations ./migrations
COPY alembic.ini ./alembic.ini
COPY config ./config
COPY prompts ./prompts

# Nunca se copian `credentials.json`/`token.json`/`.env`/`storage_state.json`
# a la imagen (ver `.dockerignore` y `.gitignore`) -- son secretos locales
# del usuario, montados como volumen en tiempo de ejecución
# (`docker-compose.yml`), nunca embebidos en la imagen.

# Usuario sin privilegios -- ni la API ni Chromium headless necesitan root.
RUN useradd --create-home --shell /usr/sbin/nologin appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# Comando por defecto: sirve la API + dashboard. El servicio `scheduler` de
# `docker-compose.yml` sobreescribe este `command` para correr
# `python -m app.presentation.scheduler.jobs` en su lugar.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
