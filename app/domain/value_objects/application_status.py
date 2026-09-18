"""State machine for `Application.status`.

Un `Application` nace cuando se crea el draft de Gmail (ver
`application/use_cases/CreateGmailDraft` en fases posteriores), por lo que
su único estado inicial es `DRAFT_CREATED`. La única transición de negocio
restante es la confirmación manual de envío (`SENT`, Fase 8 del ROADMAP,
"Mark as Sent") — nunca un envío automático.
"""

from __future__ import annotations

from enum import StrEnum


class ApplicationStatus(StrEnum):
    DRAFT_CREATED = "DRAFT_CREATED"
    SENT = "SENT"

    def can_transition_to(self, target: ApplicationStatus) -> bool:
        """Whether moving from `self` to `target` is a legal transition."""
        return target in _VALID_TRANSITIONS[self]


_VALID_TRANSITIONS: dict[ApplicationStatus, frozenset[ApplicationStatus]] = {
    ApplicationStatus.DRAFT_CREATED: frozenset({ApplicationStatus.SENT}),
    ApplicationStatus.SENT: frozenset(),
}
