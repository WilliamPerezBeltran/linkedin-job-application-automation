"""OAuth 2.0 flow and Gmail API service builder for `app.infrastructure.gmail`
(Fase 7, ROADMAP.md).

## Cómo generar `credentials.json` (paso manual, responsabilidad del usuario)

Este módulo **nunca** puede generar `credentials.json` por sí mismo: es un
archivo con secretos (`client_id`/`client_secret`) que solo Google Cloud
Console emite, ligado a un proyecto GCP del propio usuario. Pasos exactos:

1. Ir a https://console.cloud.google.com/ y crear (o reusar) un proyecto.
2. "APIs & Services" -> "Library": habilitar la **Gmail API**.
3. "APIs & Services" -> "OAuth consent screen": tipo "External" (o
   "Internal" si es Google Workspace), agregando el propio email como "test
   user" mientras la app no esté publicada/verificada por Google.
4. "APIs & Services" -> "Credentials" -> "Create Credentials" -> "OAuth
   client ID" -> tipo de aplicación **"Desktop app"** (no "Web
   application": el flujo de este módulo usa
   `InstalledAppFlow.run_local_server`, pensado para apps de
   escritorio/CLI locales, no para un backend con redirect URI público).
5. Descargar el JSON generado y guardarlo como `credentials.json` en la raíz
   del repo (o la ruta que apunte `GMAIL_CREDENTIALS_PATH`) -- **nunca
   commitearlo** (ya está en `.gitignore`).
6. Ejecutar cualquier flujo que llame a `build_gmail_service()` por primera
   vez (p. ej. crear el primer draft real vía `GmailDraftRepository`): se
   abrirá un navegador para el consentimiento OAuth
   (`InstalledAppFlow.run_local_server`). Tras autorizar, este módulo
   persiste el token resultante en `GMAIL_TOKEN_PATH` (default
   `token.json`, gitignored, permisos `0600`) para no repetir el
   consentimiento en corridas futuras -- se refresca solo mientras el
   `refresh_token` siga vigente; si deja de poder refrescarse, se vuelve a
   disparar el flujo interactivo.

## Scope de OAuth elegido: `gmail.compose`

`https://www.googleapis.com/auth/gmail.compose` es, tras revisar el
catálogo de scopes de Gmail
(https://developers.google.com/gmail/api/auth/scopes), el **más
restrictivo disponible hoy** que alcanza para crear un draft con adjunto
(`users.drafts.create`):

- `gmail.readonly` / `gmail.metadata`: solo lectura, no permiten escribir
  nada -- no sirven para crear drafts.
- `gmail.insert`: permite *insertar* mensajes ya entregados/importados
  (`users.messages.insert`, pensado para migraciones desde otro proveedor
  de correo) -- no cubre `users.drafts.create`.
- `gmail.labels`: solo administra etiquetas, no drafts ni mensajes.
- `gmail.send`: permite enviar (`users.messages.send`) pero **no** crear ni
  administrar drafts -- no cubre lo que este proyecto necesita, y además
  habilitaría directamente el envío, que es justo lo que
  `CLAUDE.md`/`ROADMAP.md` Fase 7 prohíben automatizar en el MVP.
- `gmail.modify` / `https://mail.google.com/`: sí permiten crear drafts,
  pero son estrictamente más amplios que `gmail.compose` (acceso de
  lectura/escritura a todo el buzón, o acceso total sin restricciones) --
  violarían el principio de mínimo privilegio sin necesidad real.
- `gmail.compose`: la documentación oficial de Google lo describe como
  "Create, read, update, and delete drafts. Send messages and drafts." --
  es decir, **no existe hoy en el catálogo de Google un scope más chico**
  que separe "crear/administrar drafts" de "poder enviarlos": el propio
  scope de Google acopla ambas capacidades a nivel de permiso OAuth.
  `gmail.compose` es, por lo tanto, el mínimo disponible que cubre "crear
  un draft con adjunto" -- no una elección de conveniencia, sino el límite
  real de granularidad que Google expone.

**Importante:** que el scope *permita* llamar a `users.drafts.send` /
`users.messages.send` no significa que este proyecto lo haga. La
restricción real vive en el código, no en el scope: ni este módulo ni
`gmail_draft_repository.py` implementan, exponen, ni llaman **jamás** a
ningún endpoint de envío. Si en algún momento se quisiera envío
automático, es una función aparte, explícita, que requiere confirmación
separada del usuario antes de implementarse (`docs/agents/AGENTS.md`
sección 12) -- nunca se agrega por iniciativa propia de este módulo.

## Configuración

Mismo criterio pragmático que `app.infrastructure.llm.anthropic_provider` /
`app.infrastructure.linkedin.config`: lectura directa de variables de
entorno vía `os.getenv` + `python-dotenv`, sin crear un `Settings`
compartido nuevo.

- `GMAIL_CREDENTIALS_PATH` (default `credentials.json`, relativa al
  directorio de trabajo del proceso -- mismo criterio de default que
  `FilesystemCVRepository`/`LinkedInSessionConfig`).
- `GMAIL_TOKEN_PATH` (default `token.json`, ídem).

Ninguna de las dos rutas, ni su contenido, se loguean nunca
(`docs/agents/AGENTS.md` sección 24) -- solo eventos (`gmail.auth.*`),
nunca el token ni el `client_secret` mismos.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import Resource, build

from app.infrastructure.gmail.exceptions import (
    GmailAuthenticationError,
    GmailCredentialsNotFoundError,
)

load_dotenv()

logger = logging.getLogger(__name__)

# Scope mínimo disponible que cubre "crear un draft con adjunto" -- ver
# docstring del módulo para la investigación completa de por qué no existe
# uno más chico en el catálogo actual de Google.
GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.compose"]

_DEFAULT_CREDENTIALS_PATH = Path("credentials.json")
_DEFAULT_TOKEN_PATH = Path("token.json")

_GMAIL_API_SERVICE_NAME = "gmail"
_GMAIL_API_VERSION = "v1"

# Permisos restrictivos para `token.json` -- mismo criterio de seguridad que
# `storage_state.json` de LinkedIn (`docs/agents/AGENTS.md` sección 12): solo
# el usuario dueño del proceso puede leer/escribir el token persistido.
_TOKEN_FILE_MODE = 0o600


def resolve_credentials_path(credentials_path: Path | str | None = None) -> Path:
    """Resolves the path to `credentials.json`: the explicit argument if
    given, else `GMAIL_CREDENTIALS_PATH`, else the default."""
    if credentials_path is not None:
        return Path(credentials_path)
    return Path(os.getenv("GMAIL_CREDENTIALS_PATH", str(_DEFAULT_CREDENTIALS_PATH)))


def resolve_token_path(token_path: Path | str | None = None) -> Path:
    """Resolves the path to the persisted OAuth token: the explicit argument
    if given, else `GMAIL_TOKEN_PATH`, else the default."""
    if token_path is not None:
        return Path(token_path)
    return Path(os.getenv("GMAIL_TOKEN_PATH", str(_DEFAULT_TOKEN_PATH)))


def build_gmail_service(
    *,
    credentials_path: Path | str | None = None,
    token_path: Path | str | None = None,
) -> Resource:
    """Returns an authorized Gmail API `Resource`, running the interactive
    OAuth consent flow the first time (or whenever the persisted token
    can't be silently refreshed).

    Nunca se llama en unit tests (`docs/agents/AGENTS.md` sección 16):
    `GmailDraftRepository` acepta un cliente inyectado (`drafts_client=`)
    para tests, y esta función solo corre en el camino de producción real
    (o en un script de bootstrap manual, análogo a
    `app.infrastructure.linkedin.session.run_manual_login`).

    Puede lanzar `GmailCredentialsNotFoundError` (`credentials.json` no
    existe) o `GmailAuthenticationError` (el flujo interactivo o el refresh
    fallaron) -- ver `app.infrastructure.gmail.exceptions`.
    """
    resolved_credentials_path = resolve_credentials_path(credentials_path)
    resolved_token_path = resolve_token_path(token_path)

    credentials = _load_or_refresh_credentials(
        credentials_path=resolved_credentials_path, token_path=resolved_token_path
    )
    return build(_GMAIL_API_SERVICE_NAME, _GMAIL_API_VERSION, credentials=credentials)


def _load_or_refresh_credentials(*, credentials_path: Path, token_path: Path) -> Credentials:
    credentials = _load_cached_credentials(token_path)

    if credentials is not None and credentials.valid:
        logger.info("gmail.auth.loaded_cached_token")
        return credentials

    if credentials is not None and credentials.expired and credentials.refresh_token:
        refreshed = _try_refresh(credentials)
        if refreshed is not None:
            _persist_token(refreshed, token_path=token_path)
            return refreshed

    if not credentials_path.exists():
        raise GmailCredentialsNotFoundError(str(credentials_path))

    logger.info("gmail.auth.first_time_authorization_required")
    new_credentials = _run_interactive_authorization(credentials_path)
    logger.info("gmail.auth.authorized")
    _persist_token(new_credentials, token_path=token_path)
    return new_credentials


def _load_cached_credentials(token_path: Path) -> Credentials | None:
    if not token_path.exists():
        return None
    # `google-auth` publica `py.typed`, pero `Credentials.from_authorized_user_file`
    # no tiene anotaciones propias -- el `type: ignore` de la línea de abajo
    # es por eso, no por falta de manejo de errores (ver el `except` que la
    # rodea: mypy evalúa `no-untyped-call` según el módulo que *llama*, no
    # el que *define* la función).
    try:
        credentials: Credentials = Credentials.from_authorized_user_file(  # type: ignore[no-untyped-call]
            str(token_path), GMAIL_SCOPES
        )
    except (ValueError, AttributeError):
        # `from_authorized_user_file` lanza `ValueError` (incluida
        # `json.JSONDecodeError`, subclase de `ValueError`) si el archivo no
        # tiene el formato esperado. También puede lanzar `AttributeError`
        # si el contenido es JSON válido pero no es un objeto (`[]`,
        # `null`, `42`, `"texto"`) -- el SDK internamente llama
        # `info.keys()` sobre ese valor sin validar antes que sea un
        # `dict` (hallazgo adicional de `code-reviewer`, segunda ronda de
        # revisión de Fase 7). Ambos son casos reales y esperables de
        # infraestructura no confiable (un `token.json` truncado por un
        # crash a mitad de `_persist_token`, o editado a mano por el
        # usuario), no un bug. Se tratan igual que un token cacheado
        # simplemente ausente: se cae al flujo normal de
        # refresh/reautorización de más abajo en vez de propagar el error
        # crudo del SDK (hallazgo original de `code-reviewer`,
        # revisión de Fase 7: el resto de este módulo nunca deja escapar un
        # tipo crudo del SDK, este camino no debía ser la excepción).
        logger.info("gmail.auth.cached_token_unreadable")
        return None
    return credentials


def _try_refresh(credentials: Credentials) -> Credentials | None:
    try:
        credentials.refresh(Request())  # type: ignore[no-untyped-call]
    except RefreshError:
        # No es un fallo fatal acá: cae al flujo interactivo de más abajo
        # (mismo criterio que un token.json ausente) en vez de propagar --
        # un refresh_token revocado/expirado es un caso esperable (el
        # usuario revocó acceso, o pasó demasiado tiempo), no un bug.
        logger.info("gmail.auth.token_refresh_failed")
        return None
    logger.info("gmail.auth.token_refreshed")
    return credentials


def _run_interactive_authorization(credentials_path: Path) -> Credentials:
    try:
        flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), GMAIL_SCOPES)
        result: Credentials = flow.run_local_server(port=0)
    except Exception as exc:
        # Envuelve cualquier fallo del SDK (red, browser no disponible,
        # consentimiento denegado por el usuario, credentials.json con
        # forma inválida) -- mismo criterio que
        # `AnthropicProvider._complete` envolviendo el SDK de Anthropic.
        # Nunca se loguea el contenido de `credentials_path` ni del error
        # crudo del SDK, que podría incluir fragmentos de la respuesta
        # HTTP de Google.
        raise GmailAuthenticationError(
            "The interactive Gmail OAuth authorization flow failed."
        ) from exc
    return result


def _persist_token(credentials: Credentials, *, token_path: Path) -> None:
    """Persists the OAuth token to `token_path` with owner-only permissions
    (`0600`) -- mismo criterio de seguridad que `storage_state.json` de
    LinkedIn. Nunca loguea el contenido del token.

    El archivo se crea ya con el modo `0600` (`os.open` con el modo pedido,
    en vez de `Path.write_text` seguido de un `os.chmod` posterior) para no
    dejar una ventana, aunque breve, en la que el archivo exista con los
    permisos por defecto del proceso (típicamente más permisivos según el
    `umask`) antes de restringirlos -- hallazgo de `code-reviewer` en la
    revisión de Fase 7.
    """
    token_json: str = credentials.to_json()  # type: ignore[no-untyped-call]
    fd = os.open(token_path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, _TOKEN_FILE_MODE)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as token_file:
            token_file.write(token_json)
    finally:
        # `os.chmod` cubre el caso de un `token_path` preexistente con
        # permisos más laxos: `os.open` con `O_CREAT` no cambia el modo de
        # un archivo que ya existía (el `mode` solo aplica a la creación).
        os.chmod(token_path, _TOKEN_FILE_MODE)
