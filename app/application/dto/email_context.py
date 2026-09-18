"""`EmailContext`: the DTO passed into `LLMProvider.generate_email()`.

Ver `docs/decisions/005-llm-provider-interface.md` (ADR-005) sección 3 para
la decisión completa. `GenerateApplicationEmail`
(`backend-engineer`/`llm-agent`, `app/application/use_cases/
generate_application_email.py`, todavía no creado) construye este DTO a
partir de `JobAnalysis` (skills/lenguajes/frameworks/seniority/job_type ya
detectados en Fase 3), `Job.content`/`Job.author` (el post original), y el
resumen de CV que resuelve `cv-matching-agent` (Fase 4) contra
`JobAnalysis.recommended_cv`.

Mismo criterio que `RawFeedPost`/`JobAnalysisResult`: plain data carrier,
dataclass congelado, sin invariantes de negocio.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EmailContext:
    """Input the Email Generator LLM needs to draft a single application email.

    - `cv_summary`: un resumen corto ya redactado del CV (3-5 líneas), no
      una ruta a PDF. Requisito explícito de `TOKEN_OPTIMIZATION.md` §7
      ("No mandar el PDF completo del CV... mandar un resumen corto ya
      redactado... se escribe una sola vez por CV, no se regenera por cada
      oferta") y de `ROADMAP.md` Fase 5. Lo produce `cv-matching-agent`
      (`app/application/cv/`); `GenerateApplicationEmail` solo lo resuelve y
      lo pasa como `str` ya resuelto — nunca una ruta de archivo.
    - `job_content`: el texto original del post (posiblemente truncado, ver
      `TOKEN_OPTIMIZATION.md` §3), no solo campos estructurados. `JobAnalysis`
      no tiene `company`/`role_title` como campos propios; pasar el post
      completo permite que el LLM redacte con datos concretos citados del
      propio texto en vez de inventarlos (ver ADR-005 sección 3,
      `DEBT-ADR005-02`).
    - `author`: `Job.author`, señal adicional de bajo costo (a quién
      dirigirse si `job_content` no lo deja claro).
    - Deliberadamente sin `url` ni `published_at`: no aportan valor a la
      redacción de un email de postulación (YAGNI, `ENGINEERING_STANDARDS.md`
      §31).
    """

    job_type: str
    seniority: str | None
    skills: tuple[str, ...]
    languages: tuple[str, ...]
    frameworks: tuple[str, ...]
    author: str
    job_content: str
    cv_summary: str
