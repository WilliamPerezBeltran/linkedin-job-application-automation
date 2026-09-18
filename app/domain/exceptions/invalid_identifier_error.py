"""Raised when a string cannot be parsed into an entity identifier."""

from __future__ import annotations

from app.domain.exceptions.domain_error import DomainError


class InvalidIdentifierError(DomainError):
    """The provided value is not a valid identifier for the given entity."""

    def __init__(self, *, entity_name: str, raw_value: str) -> None:
        self.entity_name = entity_name
        self.raw_value = raw_value
        super().__init__(f"Invalid {entity_name} identifier: {raw_value!r}")
