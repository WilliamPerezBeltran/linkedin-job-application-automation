"""Raised when an entity attempts an illegal status transition."""

from __future__ import annotations

from app.domain.exceptions.domain_error import DomainError


class InvalidStateTransitionError(DomainError):
    """A status transition was attempted that the entity does not allow.

    Se reutiliza para cualquier entidad con una máquina de estados propia
    (`Job`, `Application`) en vez de crear una subclase por entidad, porque
    el significado del error (transición no permitida desde el estado
    actual) es idéntico en ambos casos — solo cambian los valores.
    """

    def __init__(
        self,
        *,
        entity_name: str,
        entity_id: str,
        current_status: str,
        target_status: str,
    ) -> None:
        self.entity_name = entity_name
        self.entity_id = entity_id
        self.current_status = current_status
        self.target_status = target_status
        super().__init__(
            f"{entity_name} {entity_id!r} cannot transition from "
            f"{current_status!r} to {target_status!r}"
        )
