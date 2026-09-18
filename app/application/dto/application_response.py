"""`ApplicationResponse`: API response DTO for `POST /api/jobs/{id}/create-draft`
(Fase 7, `ROADMAP.md`, segunda ronda).

Deliberadamente un DTO nuevo, no una extensión de `JobDetailResponse`/
`JobSummaryResponse`: `Application` (`app/domain/entities/application.py`) es
una entidad de dominio distinta de `Job`/`JobAnalysis` -- con su propio ciclo
de vida (`DRAFT_CREATED -> SENT`, Fase 8) y su propio identificador
(`ApplicationId`) -- así que mezclar sus campos dentro de la respuesta de
`Job` acoplaría dos recursos HTTP distintos en un mismo contrato. Mismo
criterio de "un DTO por entidad de dominio expuesta" que ya separa
`JobSummaryResponse`/`JobDetailResponse` de `Job`/`JobAnalysis`.

`gmail_url` (decisión de producto de `backend-engineer`, Fase 7 segunda
ronda -- no está fijada en ningún ADR ni en `ROADMAP.md` con este detalle
exacto): un link directo al draft en la interfaz web de Gmail,
`https://mail.google.com/mail/u/0/#drafts/{gmail_draft_id}`, para que
`frontend-agent` pueda ofrecer un botón "Open in Gmail" sin tener que
conocer ni construir ese formato de URL por su cuenta -- la Gmail API no
devuelve una URL web utilizable en la respuesta de `users.drafts.create`
(`GmailDraftRepository.create_draft` solo expone el `id` del draft), así que
construir esa URL es responsabilidad de quien sí conoce el formato público
de Gmail. El `/u/0/` asume la primera cuenta de Google logueada en el
navegador del usuario -- una limitación conocida y aceptada: si el usuario
tiene múltiples cuentas de Gmail logueadas y la cuenta autorizada por OAuth
(`GMAIL_CREDENTIALS_PATH`/`GMAIL_TOKEN_PATH`) no es la primera, el link
puede abrir la bandeja equivocada y requerir que el usuario cambie de cuenta
manualmente en Gmail; no hay forma de resolver el índice de cuenta correcto
desde la respuesta de la API de Gmail sin una llamada adicional, así que no
se resuelve acá (YAGNI -- se puede revisar si se vuelve un problema real en
uso).

`gmail_url` es `None` cuando `gmail_draft_id` es `None` -- caso hoy
inalcanzable en la práctica (`CreateGmailDraft.execute_one` siempre setea
`gmail_draft_id` con el valor devuelto por `EmailDraftRepository.create_draft`,
que nunca es `None`; el campo es opcional en el dominio solo por si más
adelante se necesita modelar un draft eliminado externamente en Gmail), pero
se maneja explícitamente para no mentir con una URL que no apunta a ningún
draft real.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.domain.value_objects.application_status import ApplicationStatus

_GMAIL_DRAFT_URL_TEMPLATE = "https://mail.google.com/mail/u/0/#drafts/{gmail_draft_id}"


class ApplicationResponse(BaseModel):
    id: str = Field(..., description="Application id (UUID as string).")
    job_id: str = Field(..., description="Id of the Job this application was created for.")
    email: str = Field(..., description="Recipient email address the draft was addressed to.")
    subject: str = Field(..., description="Email subject used in the Gmail draft.")
    body: str = Field(..., description="Email body used in the Gmail draft.")
    cv_path: str = Field(..., description="CV catalog path attached to the draft.")
    gmail_draft_id: str | None = Field(default=None, description="Gmail draft id.")
    gmail_url: str | None = Field(
        default=None,
        description=(
            "Direct link to open the draft in the Gmail web UI. None if "
            "gmail_draft_id is None. See module docstring for the exact "
            "URL format and its known limitation with multiple Google accounts."
        ),
    )
    status: ApplicationStatus
    sent_at: datetime | None = Field(
        default=None, description="Set once the user manually confirms the send (Fase 8)."
    )


def build_gmail_url(gmail_draft_id: str | None) -> str | None:
    """Builds the Gmail web UI URL for `gmail_draft_id`, or `None` if there is none.

    Free function (not a DTO classmethod) to match the existing mapping
    style in this codebase -- `app/presentation/api/routes/jobs.py` already
    maps domain entities to response DTOs via free functions
    (`_to_summary`/`_to_detail`), not via constructors/classmethods on the
    DTOs themselves. See the module docstring for the exact URL format and
    its known limitation with multiple Google accounts.
    """
    if gmail_draft_id is None:
        return None
    return _GMAIL_DRAFT_URL_TEMPLATE.format(gmail_draft_id=gmail_draft_id)
