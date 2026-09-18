"""`EditGeneratedEmailRequest`: request body DTO for `PATCH
/api/jobs/{id}/email` (Fase 6, `ROADMAP.md` punto 3 -- "permitir editar
manualmente el subject/body generado antes de crear el draft").

Validado en el boundary HTTP (Pydantic), antes de llegar a
`app/presentation/api/routes/jobs.py`: ambos campos son requeridos y no
pueden quedar vacíos tras `strip()` -- un `subject`/`body` compuesto solo de
espacios pasaría un `Field(min_length=1)` plano pero igual violaría el
invariante que `JobAnalysis.record_generated_email` exige
(`app/domain/entities/job_analysis.py`, `_require_non_empty`). Falla acá con
un `422` automático de FastAPI en vez de propagar hasta el dominio, que es
el criterio ya establecido en este proyecto para "input del cliente
inválido" (ENGINEERING_STANDARDS.md -- validar en los boundaries).

Deliberadamente no reusa `GeneratedEmail`
(`app/application/dto/generated_email.py`): ese DTO es la salida del
`LLMProvider` (sin invariantes propias, ver su docstring), mientras que este
es un input del cliente HTTP que sí necesita validar formato -- mezclar
ambos acoplaría la capa de presentación a un contrato pensado para la
integración LLM.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class EditGeneratedEmailRequest(BaseModel):
    subject: str = Field(..., min_length=1, description="Edited email subject.")
    body: str = Field(..., min_length=1, description="Edited email body.")

    @field_validator("subject", "body")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value
