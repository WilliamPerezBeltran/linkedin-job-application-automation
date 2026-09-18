"""`GeneratedEmail`: the DTO returned by `LLMProvider.generate_email()`.

Ver `docs/decisions/005-llm-provider-interface.md` (ADR-005) sección 3.
`GenerateApplicationEmail` traduce este DTO a dominio llamando
`JobAnalysis.record_generated_email(subject=result.subject,
body=result.body)` (método ya implementado en `domain-engineer`, exige
`recommended_cv` previo).

Mismo criterio que el resto de DTOs de este módulo: plain data carrier,
dataclass congelado, sin invariantes de negocio (ni siquiera "no vacío" —
esa validación, si aplica, vive en el modelo Pydantic interno del proveedor
concreto o en la entidad de dominio que lo consume).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GeneratedEmail:
    """The subject and body of a single application email, as drafted by the LLM."""

    subject: str
    body: str
