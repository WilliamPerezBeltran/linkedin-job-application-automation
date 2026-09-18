"""`GmailDraftRepository`: `EmailDraftRepository` implementation backed by
the real Gmail API (Fase 7, ROADMAP.md).

Satisface `app.application.interfaces.email_draft_repository.EmailDraftRepository`
por structural typing (no hereda del `Protocol`) -- mismo criterio que
`AnthropicProvider`/`SQLAlchemyJobRepository`: el SDK de Google nunca se
filtra hacia `application/` ni `domain/`.

## Resolución de `cv_path` contra el disco real

`cv_path` llega tal como `CreateGmailDraft` lo obtuvo de
`CVProfile.file` (`app/application/cv/cv_profile.py`): una ruta **declarada
en `config/cvs.yaml`, relativa a la raíz del repo** (p. ej.
`cvs/java/william-java.pdf`), nunca una ruta absoluta ya resuelta en disco
-- ver el docstring de ambos módulos, que dejan esa responsabilidad
explícitamente en manos de este repositorio.

Se resuelve con el mismo criterio de `base_dir` que
`app.infrastructure.cv.filesystem_cv_repository.FilesystemCVRepository`
usa para validar que el `file` de cada entrada del catálogo existe: por
default, `Path.cwd()` (el directorio de trabajo del proceso, asumido como
la raíz del repo -- misma asunción que ya hace el resto del proyecto,
`pyproject.toml` fija `pythonpath = ["."]` para pytest). `base_dir` es
constructor-configurable (`base_dir=`) para que los tests no dependan del
cwd real del proceso que corre la suite, mismo patrón que
`FilesystemCVRepository.__init__`.

## Defensa en profundidad sobre `cv_path`

`FilesystemCVRepository` documenta una deuda técnica conocida
(`DEBT-CV-01`): no verifica que el `file` declarado en `config/cvs.yaml` no
escape de `base_dir` vía path traversal (`file: ../../../etc/passwd`),
aceptado en Fase 4 porque ese YAML es configuración local editada por el
propio usuario, no input de red. Esta capa -- infraestructura, el límite de
confianza más externo antes de leer un archivo real del disco y adjuntarlo
a un email -- sí agrega esa verificación (`_resolve_attachment_path`) antes
de abrir el archivo, sin repetir ni reemplazar la validación (inexistente)
de `cv-matching-agent`: es una defensa adicional, no una corrección de su
deuda documentada.

## Contrato de envío: nunca implementado

Este módulo llama **exclusivamente** a `users.drafts.create` (vía
`_GoogleAPIDraftsClient.create_draft`, el único método que toca el SDK real
de Gmail). No existe, ni existirá, ningún método `send`/`send_draft` en
esta clase -- instrucción no negociable de `ROADMAP.md` Fase 7 punto 7 y
`docs/agents/AGENTS.md` sección 12. El scope OAuth con el que se autoriza
la aplicación (`gmail.compose`, ver `app.infrastructure.gmail.gmail_client`)
técnicamente permitiría llamar a un endpoint de envío, pero el código de
esta clase nunca lo hace.
"""

from __future__ import annotations

import base64
import logging
import os
import unicodedata
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any, Protocol

from googleapiclient.discovery import Resource
from googleapiclient.errors import HttpError

from app.domain.value_objects.email_address import EmailAddress
from app.infrastructure.gmail.exceptions import (
    GmailAttachmentNotFoundError,
    GmailAttachmentPathTraversalError,
    GmailDraftCreationError,
    GmailInvalidMessageContentError,
)
from app.infrastructure.gmail.gmail_client import build_gmail_service

logger = logging.getLogger(__name__)

_GMAIL_USER_ID = "me"


class _DraftsClient(Protocol):
    """Minimal seam onto the Gmail API surface `GmailDraftRepository` needs.

    Mismo criterio que `_CompletionClient` en
    `app.infrastructure.llm.anthropic_provider`: deliberadamente angosto
    (un solo método) para que los tests puedan inyectar un doble de prueba
    sin construir un `googleapiclient.discovery.Resource` real (sin red,
    sin OAuth, sin costo) ni pelear con la interfaz fluida/dinámica que ese
    SDK expone (`service.users().drafts().create(...).execute()`). El
    código de producción obtiene esto envolviendo el `Resource` real
    (construido por `gmail_client.build_gmail_service`) en
    `_GoogleAPIDraftsClient`, más abajo.
    """

    def create_draft(self, *, raw_message: str) -> dict[str, Any]:
        """Calls `users.drafts.create` and returns the raw JSON response.

        `raw_message` ya viene codificado en base64url (`message.raw`,
        como exige la API de Gmail) -- este método no hace ninguna
        transformación adicional, solo transporta la llamada.
        """
        ...


class _GoogleAPIDraftsClient:
    """Adapts a real, already-authorized Gmail API `Resource` to
    `_DraftsClient`.

    Único punto de este módulo (junto con `gmail_client.py`) que importa
    símbolos concretos de `googleapiclient` -- y el único método de todo
    `app.infrastructure.gmail` que efectivamente llama a un endpoint de
    escritura de la API de Gmail. Deliberadamente acotado a
    `users().drafts().create(...)`: nunca `drafts().send(...)` ni
    `messages().send(...)`.
    """

    def __init__(self, service: Resource) -> None:
        self._service = service

    def create_draft(self, *, raw_message: str) -> dict[str, Any]:
        request = (
            self._service.users()
            .drafts()
            .create(userId=_GMAIL_USER_ID, body={"message": {"raw": raw_message}})
        )
        response: dict[str, Any] = request.execute()
        return response


class GmailDraftRepository:
    """`EmailDraftRepository` implementation using the real Gmail API.

    Ownership: `gmail-agent` (`app/infrastructure/gmail/**`). No hereda de
    `EmailDraftRepository` (`Protocol`) -- lo satisface por forma, mismo
    criterio que el resto de adaptadores de infraestructura del proyecto.
    """

    def __init__(
        self,
        *,
        drafts_client: _DraftsClient | None = None,
        base_dir: Path | str | None = None,
        credentials_path: Path | str | None = None,
        token_path: Path | str | None = None,
    ) -> None:
        """Builds the repository.

        `drafts_client`: inyección de dependencia para tests -- cuando se
        pasa, `credentials_path`/`token_path` se ignoran porque el cliente
        ya está construido (mismo patrón que `AnthropicProvider(client=...)`).
        Cuando es `None` (uso normal en producción), se construye un
        `googleapiclient.discovery.Resource` real vía
        `gmail_client.build_gmail_service` (que puede disparar el flujo
        interactivo de OAuth si hace falta) y se envuelve en
        `_GoogleAPIDraftsClient`.

        `base_dir`: directorio contra el que se resuelve `cv_path` (ver
        docstring del módulo). Por defecto, el directorio de trabajo del
        proceso -- mismo criterio que
        `FilesystemCVRepository.__init__`. Los tests pasan un `base_dir`
        explícito (un directorio temporal) para no depender del cwd real.
        """
        if drafts_client is not None:
            self._drafts_client: _DraftsClient = drafts_client
        else:
            service = build_gmail_service(credentials_path=credentials_path, token_path=token_path)
            self._drafts_client = _GoogleAPIDraftsClient(service)
        self._base_dir = Path(base_dir) if base_dir is not None else Path.cwd()

    def create_draft(self, *, to: EmailAddress, subject: str, body: str, cv_path: str) -> str:
        """See `EmailDraftRepository.create_draft`.

        Nunca envía el draft creado -- ver docstring del módulo, sección
        "Contrato de envío: nunca implementado".
        """
        _reject_embedded_control_characters(field_name="subject", value=subject)
        attachment_path = self._resolve_attachment_path(cv_path)
        raw_message = _build_raw_message(
            to=to, subject=subject, body=body, attachment_path=attachment_path
        )

        try:
            response = self._drafts_client.create_draft(raw_message=raw_message)
        except HttpError as exc:
            # Nunca se loguea `to`/`subject`/`body` acá (contienen datos
            # personales del post/candidato) -- mensaje genérico a
            # propósito, mismo criterio que `AnthropicProvider._complete`.
            raise GmailDraftCreationError(
                "The Gmail API rejected the draft creation request."
            ) from exc

        draft_id = response.get("id")
        if not isinstance(draft_id, str) or not draft_id:
            raise GmailDraftCreationError(
                "The Gmail API returned a draft creation response without a usable 'id'."
            )

        logger.info("gmail.draft.created")
        return draft_id

    def _resolve_attachment_path(self, cv_path: str) -> Path:
        """Resolves `cv_path` (catalog-relative, ver docstring del módulo)
        against `self._base_dir`, and validates it defensively before it is
        ever opened for reading.

        Dos chequeos, en este orden:

        1. Path traversal: el resultado de `base_dir / cv_path` debe seguir
           contenido dentro de `base_dir` una vez resuelto
           (`Path.resolve()`, que colapsa `..`/symlinks) -- defensa en
           profundidad documentada en el docstring del módulo
           (`GmailAttachmentPathTraversalError`).
        2. Existencia/legibilidad: el archivo debe existir y ser legible
           por el proceso actual (`os.access(..., os.R_OK)`) --
           `GmailAttachmentNotFoundError`, nunca un `FileNotFoundError`
           genérico ni un fallo tardío al construir el MIME.
        """
        resolved_base_dir = self._base_dir.resolve()
        resolved_path = (self._base_dir / cv_path).resolve()

        if not resolved_path.is_relative_to(resolved_base_dir):
            raise GmailAttachmentPathTraversalError(cv_path, str(resolved_path))

        if not resolved_path.is_file() or not os.access(resolved_path, os.R_OK):
            raise GmailAttachmentNotFoundError(cv_path, str(resolved_path))

        return resolved_path


def _reject_embedded_control_characters(*, field_name: str, value: str) -> None:
    """Rejects `value` if it contains any embedded Unicode control character
    (categoría `Cc`: `\\x00`-`\\x1f` y `\\x7f`-`\\x9f`) -- hallazgo de
    `security-agent`, revisión de seguridad de Fase 7 (segunda ronda, tras
    una primera versión de este chequeo que solo cubría `\\r`/`\\n` --
    incompleta, ver más abajo).

    Solo se aplica a `subject`: `to` es un `EmailAddress` (value object de
    dominio) cuyo patrón ya está anclado (`^...$`) y no admite espacios en
    blanco ni caracteres de control, así que no puede llegar acá con
    ninguno embebido. `body` va en `MIMEText`, no en un header MIME, así
    que un carácter de control ahí (incluido un salto de línea legítimo)
    es contenido del cuerpo, no una inyección de headers.

    La librería `email` del stdlib ya detecta *algunos* caracteres de
    control al serializar el mensaje (`Generator`/`BytesGenerator` lanzan
    `HeaderParseError`/`HeaderWriteError` no solo para `\\r`/`\\n`, sino
    también, entre otros, para `\\x0b` (vertical tab), `\\x0c` (form feed) y
    los separadores de información `\\x1c`-`\\x1e`) -- la inyección de
    headers en sí nunca llega a producir un mensaje MIME válido -- pero ese
    error no envuelto: (a) no es un `GmailInfrastructureError`, así que se
    cuela sin traducir más allá de este módulo (el
    `except GmailInfrastructureError` de
    `app/presentation/api/routes/jobs.py` no lo captura), y (b) su propio
    mensaje incluye el contenido crudo de `value`, exactamente lo que
    `docs/agents/AGENTS.md` sección 24 prohíbe que termine en un log de
    excepción no manejada.

    La primera versión de este chequeo (revisión de `code-reviewer`, misma
    ronda) solo probaba `"\\r" in value or "\\n" in value`, dejando un hueco
    equivalente para el resto de esos caracteres (`\\x0b`, `\\x0c`,
    `\\x1c`-`\\x1e`, reproducidos empíricamente contra el mismo stdlib). En
    vez de seguir enumerando a mano cada carácter que la librería `email`
    trate de forma especial, se generaliza a "cualquier carácter Unicode de
    categoría `Cc`" (`unicodedata.category(ch) == "Cc"`), que cubre `\\r`/
    `\\n` y los demás casos reproducidos sin depender de una lista cerrada
    que quede desactualizada si el stdlib cambia su propio criterio. Se
    falla rápido acá, con una excepción propia de esta jerarquía y sin
    filtrar el contenido de `value` en el mensaje.
    """
    if any(unicodedata.category(character) == "Cc" for character in value):
        raise GmailInvalidMessageContentError(field_name=field_name)


def _build_raw_message(*, to: EmailAddress, subject: str, body: str, attachment_path: Path) -> str:
    """Builds a MIME multipart message (plain-text body + PDF attachment)
    and returns it base64url-encoded, as `users.drafts.create` expects in
    `message.raw`.

    `body` se trata como texto plano (`MIMEText(body, "plain", ...)`), no
    HTML: `prompts/email-generation/v1.txt` pide explícitamente al LLM
    "cuerpo completo del email... texto plano sin markdown", así que ese es
    el contrato real que produce `GenerateApplicationEmail` -- este
    repositorio no reinterpreta `body` como HTML.
    """
    message = MIMEMultipart()
    message["to"] = str(to)
    message["subject"] = subject
    message.attach(MIMEText(body, "plain", "utf-8"))

    pdf_bytes = attachment_path.read_bytes()
    attachment = MIMEApplication(pdf_bytes, _subtype="pdf")
    attachment.add_header("Content-Disposition", "attachment", filename=attachment_path.name)
    message.attach(attachment)

    raw_bytes = base64.urlsafe_b64encode(message.as_bytes())
    return raw_bytes.decode("ascii")
