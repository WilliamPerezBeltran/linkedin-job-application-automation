"""Raised for field-level invariant violations without a dedicated exception.

Se usa para invariantes simples de un único campo (ej. `content_hash` vacío,
`match_score` fuera de `[0, 1]`, `cv_path` con segmentos `..`) donde crear una
excepción dedicada por campo sería sobreingeniería (ENGINEERING_STANDARDS
§31). Si un invariante concreto necesita ser distinguido programáticamente
por quien lo captura, se promueve a su propia subclase de `DomainError`.
"""

from __future__ import annotations

from app.domain.exceptions.domain_error import DomainError


class InvalidDomainValueError(DomainError):
    """A field value violates a domain invariant."""

    def __init__(self, *, field_name: str, reason: str) -> None:
        self.field_name = field_name
        self.reason = reason
        super().__init__(f"Invalid value for {field_name!r}: {reason}")
