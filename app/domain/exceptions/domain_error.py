"""Base exception for all domain-level invariant violations."""

from __future__ import annotations


class DomainError(Exception):
    """Root of the domain exception hierarchy.

    Nunca se lanza directamente: siempre a través de una subclase específica
    que exprese qué invariante de negocio fue violado. Catch-all genérico
    prohibido (`except Exception: pass`) en cualquier capa que consuma el
    dominio — se debe capturar `DomainError` o una subclase concreta.
    """
