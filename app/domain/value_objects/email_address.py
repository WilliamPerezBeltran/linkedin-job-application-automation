"""Value object that guarantees a validated, normalized email address.

La validación vive aquí porque es una regla de negocio del dominio (un
`Job`/`Application` con un email mal formado es un dato inválido), no algo
que se delega a Gmail ni a una librería externa de validación (ver
`CLAUDE.md` / `ENGINEERING_STANDARDS.md` §1).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.domain.exceptions.invalid_email_address_error import InvalidEmailAddressError

# Regex simple y deliberadamente no exhaustivo (no implementa RFC 5322
# completo): exige `local@domain.tld` con al menos un punto en el dominio.
# Suficiente para rechazar basura evidente extraída del feed de LinkedIn sin
# arrastrar una dependencia externa de validación de emails.
_EMAIL_PATTERN = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


@dataclass(frozen=True, slots=True)
class EmailAddress:
    """A syntactically valid, lowercase-normalized email address."""

    value: str

    def __post_init__(self) -> None:
        normalized = self.value.strip().lower()
        if not _EMAIL_PATTERN.match(normalized):
            raise InvalidEmailAddressError(self.value)
        # dataclass es frozen: usar object.__setattr__ para normalizar el
        # valor almacenado sin violar la inmutabilidad hacia el exterior.
        object.__setattr__(self, "value", normalized)

    def __str__(self) -> str:
        return self.value
