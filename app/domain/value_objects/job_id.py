"""Identifier value object for the `Job` entity."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from app.domain.exceptions.invalid_identifier_error import InvalidIdentifierError


@dataclass(frozen=True, slots=True)
class JobId:
    """Wraps a `UUID` so `Job.id` is a distinct type instead of a raw string.

    Value object: dos instancias con el mismo `value` son iguales
    (comportamiento por defecto de un dataclass `frozen` comparando por
    campos), inmutable una vez creado.
    """

    value: UUID

    @classmethod
    def new(cls) -> JobId:
        """Generates a fresh, random identifier for a newly created Job."""
        return cls(uuid4())

    @classmethod
    def of(cls, raw: str) -> JobId:
        """Parses a `JobId` from its string representation.

        Raises `InvalidIdentifierError` if `raw` is not a valid UUID —
        used by repositories/use cases when rehydrating an id received from
        an external boundary (API path param, database row).
        """
        try:
            return cls(UUID(raw))
        except (ValueError, AttributeError, TypeError) as exc:
            raise InvalidIdentifierError(entity_name="Job", raw_value=raw) from exc

    def __str__(self) -> str:
        return str(self.value)
