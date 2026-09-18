"""Infrastructure-level exceptions for `app.infrastructure.gmail`.

Deliberadamente **no** heredan de
`app.domain.exceptions.domain_error.DomainError`: esa jerarquía está
reservada a violaciones de invariantes de *negocio* (`Job`, `EmailAddress`,
etc.). Todo lo que ocurre en este módulo es infraestructura no confiable --
el flujo OAuth de Google, la API real de Gmail, y el filesystem local donde
vive el CV a adjuntar -- mismo criterio ya usado por
`app.infrastructure.linkedin.exceptions`/`app.infrastructure.llm.exceptions`/
`app.infrastructure.cv.exceptions`: raíz abstracta + subclases concretas,
nunca se lanza la raíz directamente, y nunca `except Exception: pass`
(`docs/agents/AGENTS.md` principio 15 y sección 23).

A diferencia de `app.infrastructure.llm.exceptions` (ADR-006), esta
jerarquía **no** hereda de ninguna clase homónima en `app/application/`:
`app.application.interfaces.email_draft_repository.EmailDraftRepository`
no declara ni captura ninguna excepción por nombre -- su docstring es
explícito en que "puede propagar excepciones de infraestructura propias de
`gmail-agent`... este Protocol no las declara por nombre" -- así que no hay
hoy ningún módulo de `application/` que necesite importar estas clases por
nombre (a diferencia de `CVInfrastructureError`/`LLMProviderError`, que sí
son capturadas explícitamente por `GenerateApplicationEmail`/
`AnalyzeJobPost`). Si eso cambia, introducir un boundary en
`app/application/interfaces/` (mismo patrón que ADR-006) es una extensión
directa -- no se adelanta ahora por YAGNI.
"""

from __future__ import annotations


class GmailInfrastructureError(Exception):
    """Root of every exception raised by `app.infrastructure.gmail`.

    Nunca se lanza directamente -- siempre una subclase concreta, para que
    quien la capture pueda distinguir "faltan credenciales OAuth" de "la
    API de Gmail rechazó la creación del draft" de "el CV a adjuntar no
    existe en disco" sin recurrir a `except Exception`.
    """


class GmailAuthenticationError(GmailInfrastructureError):
    """El flujo OAuth 2.0 (autorización interactiva o refresh de token) falló.

    Envuelve cualquier excepción de `google-auth`/`google-auth-oauthlib`
    (p. ej. `google.auth.exceptions.RefreshError`, o un fallo de red/browser
    durante `InstalledAppFlow.run_local_server`) vía `raise ... from exc` --
    nunca deja propagar el tipo crudo del SDK. Nunca incluye en su mensaje
    el contenido de `token.json`/`credentials.json` (`docs/agents/AGENTS.md`
    sección 24).
    """


class GmailCredentialsNotFoundError(GmailInfrastructureError):
    """`credentials.json` (o la ruta de `GMAIL_CREDENTIALS_PATH`) no existe.

    Este módulo nunca puede generar ese archivo por sí mismo -- es
    responsabilidad del usuario crearlo en Google Cloud Console (Gmail API
    habilitada, credenciales OAuth de tipo "Desktop app") y descargarlo. Ver
    el docstring de `app.infrastructure.gmail.gmail_client`, sección "Cómo
    generar `credentials.json`", para los pasos exactos. El mensaje nunca
    incluye contenido sensible (el archivo esperado no existe, no hay nada
    sensible que filtrar).
    """

    def __init__(self, credentials_path: str) -> None:
        super().__init__(
            f"Gmail credentials file not found at '{credentials_path}'. "
            "Create OAuth 2.0 credentials (type 'Desktop app') in Google "
            "Cloud Console with the Gmail API enabled, download the JSON, "
            "and save it at this path (or point GMAIL_CREDENTIALS_PATH to "
            "it). See app.infrastructure.gmail.gmail_client's module "
            "docstring for the full steps."
        )
        self.credentials_path = credentials_path


class GmailDraftCreationError(GmailInfrastructureError):
    """La API de Gmail rechazó o falló al crear el draft.

    Envuelve `googleapiclient.errors.HttpError` (o una respuesta exitosa a
    nivel de transporte pero sin un `id` de draft utilizable) vía
    `raise ... from exc` -- nunca deja propagar el tipo crudo del SDK más
    allá de `app.infrastructure.gmail`. Nunca implica, ni implicará, un
    reintento de envío: este proyecto nunca llama a `users.drafts.send` ni
    `users.messages.send` (ver `docs/agents/AGENTS.md` sección 12).
    """


class GmailAttachmentNotFoundError(GmailInfrastructureError):
    """El `cv_path` resuelto contra el `base_dir` de este repositorio no
    existe en disco, o existe pero no es legible por el proceso actual.

    Se lanza *antes* de intentar construir el mensaje MIME/llamar a la API
    de Gmail -- un adjunto inexistente/no legible nunca debe llegar a
    generar un draft real sin CV adjunto ni fallar tarde con un error
    genérico del SDK de Google.
    """

    def __init__(self, cv_path: str, resolved_path: str) -> None:
        super().__init__(
            f"CV attachment for cv_path '{cv_path}' not found or not "
            f"readable at resolved path: {resolved_path}"
        )
        self.cv_path = cv_path
        self.resolved_path = resolved_path


class GmailInvalidMessageContentError(GmailInfrastructureError):
    """`subject` contiene un carácter de control Unicode (categoría `Cc`:
    `\\x00`-`\\x1f`/`\\x7f`-`\\x9f`, incluye `\\r`/`\\n` pero no se limita a
    ellos) embebido -- nunca debe llegar a construirse un mensaje MIME con
    eso.

    Encontrado en la revisión de seguridad de Fase 7 (`security-agent`,
    con una segunda ronda de `code-reviewer` que amplió el alcance
    original): `subject`/`body` los produce el LLM
    (`GenerateApplicationEmail`, ver `_RawEmailResponse._reject_blank` en
    `app.infrastructure.llm.anthropic_provider`), que solo valida
    "no vacío tras `strip()`" -- `strip()` no elimina un carácter de
    control *embebido* en medio del texto (solo espacios en blanco en los
    extremos), así que un `subject` como
    `"Hello\\r\\nBcc: attacker@evil.com"` pasa esa validación intacta. La
    librería `email` de Python (`Generator`/`BytesGenerator`, policy
    `compat32`) ya detecta varios de estos caracteres (no solo `\\r`/`\\n`,
    también p. ej. `\\x0b`/`\\x0c`/`\\x1c`-`\\x1e`) y lanza
    `email.errors.HeaderParseError`/`HeaderWriteError` al serializar el
    mensaje -- por lo que la inyección de headers en sí *no* es explotable
    (el intento de inyectar `Bcc:`/headers falsos nunca llega a producir un
    mensaje MIME válido) -- pero, sin este chequeo explícito, ese error
    crudo del stdlib se propagaba sin envolver más allá de
    `GmailDraftRepository.create_draft` (no es un `GmailInfrastructureError`,
    así que el `except GmailInfrastructureError` de
    `app/presentation/api/routes/jobs.py` no lo capturaba -- terminaba como
    un 500 no controlado) y, más grave, su propio mensaje de error
    (`"header value appears to contain an embedded header: <subject
    completo>"` / `"folded header contains newline: <subject completo>"`)
    incluye el contenido crudo de `subject` -- justo lo que
    `docs/agents/AGENTS.md` sección 24 prohíbe que termine en un log de
    excepción no manejada.

    Este chequeo se hace *antes* de intentar construir el mensaje MIME
    (mismo criterio que `GmailAttachmentNotFoundError`/
    `GmailAttachmentPathTraversalError`: fail fast, sin depender de que el
    stdlib rechace tarde con un tipo de excepción ajeno a esta jerarquía, y
    sin enumerar a mano cada carácter que el stdlib trate de forma especial
    -- ver `_reject_embedded_control_characters` en
    `gmail_draft_repository.py`). El mensaje de esta excepción nunca
    incluye el contenido de `subject`, solo el nombre del campo rechazado,
    para no repetir el mismo problema que motivó este chequeo.
    """

    def __init__(self, *, field_name: str) -> None:
        super().__init__(
            f"'{field_name}' contains an embedded control character, which would "
            "corrupt the MIME message headers. Refusing to build the draft."
        )
        self.field_name = field_name


class GmailAttachmentPathTraversalError(GmailInfrastructureError):
    """El `cv_path` resuelto escapa del `base_dir` esperado.

    Defensa en profundidad (`ROADMAP.md` Fase 7 / deuda `DEBT-CV-01`
    documentada en `app.infrastructure.cv.filesystem_cv_repository`):
    `cv-matching-agent` no valida hoy que `config/cvs.yaml` esté libre de
    `file: ../../etc/passwd`-style traversal porque ese YAML es
    configuración local del propio usuario, no input de red. Esta capa
    (infraestructura, el límite de confianza más externo antes de leer un
    archivo real del disco y adjuntarlo a un email) sí lo verifica antes de
    abrir el archivo, sin repetir ni reemplazar la validación de
    `cv-matching-agent`.
    """

    def __init__(self, cv_path: str, resolved_path: str) -> None:
        super().__init__(
            f"cv_path '{cv_path}' resolves to '{resolved_path}', which is "
            "outside the expected base directory. Refusing to read it."
        )
        self.cv_path = cv_path
        self.resolved_path = resolved_path
