"""Domain exceptions: invariant violations raised by entities/value objects.

Ninguna de estas excepciones depende de frameworks o de infraestructura —
son parte del lenguaje de dominio y deben poder importarse desde cualquier
capa sin arrastrar FastAPI, SQLAlchemy, etc.
"""

from app.domain.exceptions.domain_error import DomainError
from app.domain.exceptions.invalid_domain_value_error import InvalidDomainValueError
from app.domain.exceptions.invalid_email_address_error import InvalidEmailAddressError
from app.domain.exceptions.invalid_identifier_error import InvalidIdentifierError
from app.domain.exceptions.invalid_state_transition_error import InvalidStateTransitionError

__all__ = [
    "DomainError",
    "InvalidDomainValueError",
    "InvalidEmailAddressError",
    "InvalidIdentifierError",
    "InvalidStateTransitionError",
]
