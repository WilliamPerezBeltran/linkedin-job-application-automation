"""Port towards Gmail draft creation, consumed by `CreateGmailDraft`
(Fase 7, ROADMAP.md).

Ya nombrado explícitamente en ADR-003 (`docs/decisions/003-feed-collector-interface.md`,
sección "Ubicación y firma") como ejemplo del criterio general de ADR-001:
los Protocols de infraestructura externa consumidos por use cases de
`backend-engineer` van en `app/application/interfaces/`, junto a
`LLMProvider`/`FeedCollector`. Este módulo aterriza esa decisión ya tomada,
no la reabre.

`Protocol` (no ABC), mismo criterio que `FeedCollector`/`LLMProvider`: un
contrato hacia un sistema externo (acá, la API de Gmail) que `application/`
orquesta sin conocer el detalle concreto. La implementación
(`GmailDraftRepository`, Fase 7, ownership `gmail-agent`) vive en
`app/infrastructure/gmail/gmail_draft_repository.py` y satisface esta
interfaz por structural typing — el SDK de Gmail/OAuth nunca se filtra hacia
`application/` ni `domain/`.

Se llama "Repository" (no "Client"/"Service") por el mismo motivo que
`FeedCollector`/`LLMProvider` no llevan sufijo `Client`: sigue la convención
ya fijada de nombrar los Protocols de `application/interfaces/` por el rol
que cumplen en el pipeline, no por el detalle técnico de transporte
(HTTP/SDK) que usa la implementación concreta.

Contrato deliberadamente mínimo, restringido a **crear** un draft — nunca
enviarlo. `CLAUDE.md` y `docs/agents/AGENTS.md` sección 12 (Gmail Agent) son
explícitos: "Do NOT automatically send emails in the MVP", el envío
(`Send`) es una acción 100% manual del usuario desde su propio Gmail. Por
eso este `Protocol` no declara -- y no debe declarar nunca, ni siquiera como
comentario de "para más adelante" -- ningún método `send`/`send_draft`/
equivalente.
"""

from __future__ import annotations

from typing import Protocol

from app.domain.value_objects.email_address import EmailAddress


class EmailDraftRepository(Protocol):
    def create_draft(self, *, to: EmailAddress, subject: str, body: str, cv_path: str) -> str:
        """Creates a Gmail draft (never sends it) and returns its Gmail draft id.

        - `to`: `EmailAddress` (value object de dominio, no `str` plano) —
          mismo criterio que el resto de `application/` reutiliza value
          objects del dominio en vez de tipos primitivos donde ya existen
          (p. ej. `EmailContext`/`Job.email`).
        - `cv_path`: **no** es una ruta absoluta ya resuelta en disco — es
          `CVProfile.file` tal cual está declarada en `config/cvs.yaml`
          (p. ej. `cvs/java/william-java.pdf`), relativa a la raíz del
          repo, tal como `CreateGmailDraft`
          (`app/application/use_cases/create_gmail_draft.py`) la obtiene de
          `CVCatalog.list_cvs()` sin modificarla. `CVProfile.file` (ver su
          propio docstring, `app/application/cv/cv_profile.py`) ya deja
          fijado explícitamente que resolverla contra el `base_dir`
          correcto para llegar a un path de filesystem adjuntable es
          responsabilidad de quien implemente este `Protocol`
          (`GmailDraftRepository`, Fase 7, `gmail-agent`) — el mismo
          `base_dir` que usó `FilesystemCVRepository` para validar que el
          archivo existe. Este contrato de aplicación no sabe nada de cómo
          se resuelve esa ruta a disco, ni de la existencia de "categorías"
          de CV, solo que `cv_path` identifica un archivo adjuntable una vez
          resuelto.
        - Retorna el `gmail_draft_id` (`str`) que la implementación concreta
          obtiene de la respuesta real de la API de Gmail. Quien llama lo
          persiste en `Application.gmail_draft_id` (ver
          `app/domain/entities/application.py`).
        - Puede propagar excepciones de infraestructura propias de
          `gmail-agent` (autenticación OAuth fallida, error de la API de
          Gmail, adjunto no encontrado en disco) — este Protocol no las
          declara por nombre (Python no tiene `raises` tipado), mismo
          criterio que `FeedCollector.collect()` con las excepciones de
          LinkedIn.
        - Nunca envía el draft creado. No existe, ni existirá en este
          Protocol, ningún método de envío — el envío es una acción manual
          del usuario, ejecutada fuera de esta aplicación, directamente en
          Gmail.
        """
        ...
