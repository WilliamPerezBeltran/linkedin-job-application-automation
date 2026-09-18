"""State machine for `Job.status`, per la máquina de estados de CLAUDE.md.

```
SCRAPED -> ANALYZED -> RELEVANT -> CV_SELECTED -> EMAIL_GENERATED -> DRAFT_CREATED -> SENT
                    -> NOT_RELEVANT   (terminal)

{SCRAPED, ANALYZED, RELEVANT, CV_SELECTED, EMAIL_GENERATED} -> IGNORED   (terminal, manual)
```

`NOT_RELEVANT` y `SENT` son estados terminales: ninguna transición sale de
ellos. El grafo de transiciones vive en este value object (qué transiciones
son abstractamente válidas); la entidad `Job` es quien decide, en el
contexto de una instancia concreta, si aplica una transición y lanza
`InvalidStateTransitionError` con el `id` del job si no es válida.

## `IGNORED`

`IGNORED` es un estado terminal adicional que representa una decisión
manual del usuario (Fase 6, dashboard de revisión: `POST
/api/jobs/{id}/ignore`), no un paso disparado por el pipeline automático.

Es alcanzable desde `SCRAPED, ANALYZED, RELEVANT, CV_SELECTED,
EMAIL_GENERATED` — es decir, desde cualquier punto de revisión humana
*antes* de que exista un draft real de Gmail. Razonamiento:

- El usuario debe poder descartar una oferta en cualquier momento anterior
  a que se cree un efecto externo real (un draft en Gmail), sin importar
  cuánto haya avanzado ya el pipeline automático (análisis, CV
  seleccionado, email generado son todos artefactos internos, descartables
  sin costo).
- `DRAFT_CREATED` y `SENT` quedan **excluidos** a propósito: en esos
  estados ya existe un draft real (o un envío real) en Gmail. "Ignorar" ahí
  no es una operación de este estado — sería borrar/descartar un draft
  existente, que es una acción distinta (de otro endpoint, competencia de
  la integración con Gmail) y no una transición de `JobStatus`.
- `NOT_RELEVANT` queda **excluido**: ya es terminal por su cuenta y ya está
  fuera del flujo activo de revisión, así que no necesita una transición
  adicional hacia `IGNORED`.
- `IGNORED` mismo queda excluido de sus propios destinos, como cualquier
  otro estado terminal (`NOT_RELEVANT`, `SENT`): ninguna transición sale de
  él.
"""

from __future__ import annotations

from enum import StrEnum


class JobStatus(StrEnum):
    SCRAPED = "SCRAPED"
    ANALYZED = "ANALYZED"
    RELEVANT = "RELEVANT"
    NOT_RELEVANT = "NOT_RELEVANT"
    CV_SELECTED = "CV_SELECTED"
    EMAIL_GENERATED = "EMAIL_GENERATED"
    DRAFT_CREATED = "DRAFT_CREATED"
    SENT = "SENT"
    IGNORED = "IGNORED"

    def can_transition_to(self, target: JobStatus) -> bool:
        """Whether moving from `self` to `target` is a legal transition."""
        return target in _VALID_TRANSITIONS[self]


_VALID_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.SCRAPED: frozenset({JobStatus.ANALYZED, JobStatus.IGNORED}),
    JobStatus.ANALYZED: frozenset({JobStatus.RELEVANT, JobStatus.NOT_RELEVANT, JobStatus.IGNORED}),
    JobStatus.RELEVANT: frozenset({JobStatus.CV_SELECTED, JobStatus.IGNORED}),
    JobStatus.NOT_RELEVANT: frozenset(),
    JobStatus.CV_SELECTED: frozenset({JobStatus.EMAIL_GENERATED, JobStatus.IGNORED}),
    JobStatus.EMAIL_GENERATED: frozenset({JobStatus.DRAFT_CREATED, JobStatus.IGNORED}),
    JobStatus.DRAFT_CREATED: frozenset({JobStatus.SENT}),
    JobStatus.SENT: frozenset(),
    JobStatus.IGNORED: frozenset(),
}
