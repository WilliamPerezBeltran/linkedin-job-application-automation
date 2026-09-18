"""Raised when a string does not conform to the accepted email format."""

from __future__ import annotations

from app.domain.exceptions.domain_error import DomainError


class InvalidEmailAddressError(DomainError):
    """The provided value is not a syntactically valid email address."""

    def __init__(self, raw_value: str) -> None:
        self.raw_value = raw_value
        super().__init__(f"Invalid email address: {raw_value!r}")
