"""Domain repository interfaces (Protocols) for persisting domain entities.

Implementaciones concretas viven en `app/infrastructure/database/repositories/`
(ownership de `database-agent`) — este paquete solo define contratos.
"""

from app.domain.repositories.job_repository import JobRepository

__all__ = [
    "JobRepository",
]
