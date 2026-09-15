# Token Budget

Límites configurados para las llamadas al LLM en Job Application Automation, y la justificación de cada uno. Mantenido por `token-optimization-agent` (ver [`.claude/agents/14_token-optimization.md`](../../.claude/agents/14_token-optimization.md)); coordinado con `llm-agent` (`06_llm.md`).

Estado: sin implementación todavía — valores de referencia a ajustar con datos reales una vez exista el Job Analyzer (ver [`ROADMAP.md`](../../ROADMAP.md) Fase 3).

## Configuración actual

```env
LLM_MAX_INPUT_TOKENS=12000
LLM_MAX_OUTPUT_TOKENS=1000
LLM_MAX_RETRIES=2
LLM_CACHE_ENABLED=true
```

| Variable | Valor | Justificación |
|---|---|---|
| `LLM_MAX_INPUT_TOKENS` | 12000 | Valor de referencia inicial — un post de LinkedIn truncado (~800-1000 caracteres, ver `TOKEN_OPTIMIZATION.md` §3) más system prompt no debería acercarse a este límite; sirve como guardrail ante contenido anómalo. |
| `LLM_MAX_OUTPUT_TOKENS` | 1000 | Cubre el JSON estructurado del Job Analyzer y el email generado por el Email Generator sin dejar margen para texto libre innecesario. |
| `LLM_MAX_RETRIES` | 2 | Backoff exponencial acotado — ver `TOKEN_OPTIMIZATION.md` §12 (LLM Agent), nunca retry infinito. |
| `LLM_CACHE_ENABLED` | true | Habilita prompt caching en el system prompt del Analyzer cuando el proveedor lo soporte. |

## Presupuesto por etapa del pipeline (a completar con datos reales)

| Etapa | Modelo previsto | Input tokens objetivo | Output tokens objetivo | Notas |
|---|---|---|---|---|
| Job Analyzer | modelo pequeño/rápido | — | — | Corre sobre el mayor volumen (todo lo que pasa el Candidate Filter). |
| CV Matcher (fallback semántico) | modelo pequeño/medio | — | — | Solo cuando el matching determinístico no es concluyente. |
| Email Generator | modelo intermedio/grande | — | — | Corre solo sobre jobs ya relevantes — menor volumen, mayor calidad exigida. |

## Historial de cambios a este presupuesto

Ninguno todavía — registrar aquí cada vez que se ajuste un límite, con fecha, razón y referencia a la entrada correspondiente en [`llm-cost-analysis.md`](./llm-cost-analysis.md).
