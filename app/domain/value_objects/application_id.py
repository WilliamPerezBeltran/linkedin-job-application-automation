"""Identifier value object for the `Application` entity."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from app.domain.exceptions.invalid_identifier_error import InvalidIdentifierError


@dataclass(frozen=True, slots=True)
class ApplicationId:
    """Wraps a `UUID` so `Application.id` is a distinct type from `JobId`."""

    value: UUID

    @classmethod
    def new(cls) -> ApplicationId:
        """Generates a fresh, random identifier for a newly created Application."""
        return cls(uuid4())

    @classmethod
    def of(cls, raw: str) -> ApplicationId:
        """Parses an `ApplicationId` from its string representation.

        Raises `InvalidIdentifierError` if `raw` is not a valid UUID.
        """
        try:
            return cls(UUID(raw))
        except (ValueError, AttributeError, TypeError) as exc:
            raise InvalidIdentifierError(entity_name="Application", raw_value=raw) from exc

    def __str__(self) -> str:
        return str(self.value)
