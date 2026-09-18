# Token Budget

Límites configurados para las llamadas al LLM en Job Application Automation, y la justificación de cada uno. Mantenido por `token-optimization-agent` (ver [`.claude/agents/14_token-optimization.md`](../../.claude/agents/14_token-optimization.md)); coordinado con `llm-agent` (`06_llm.md`).

Estado: Fase 3 (Job Analyzer) y Fase 5 (Email Generator) ya implementadas en `app/infrastructure/llm/anthropic_provider.py`. Primera revisión transversal de `token-optimization-agent` post-Fase 5: 2026-09-16 (ver entrada correspondiente en [`llm-cost-analysis.md`](./llm-cost-analysis.md)).

**Nota de método:** ninguno de los números de esta página proviene de una llamada real a la API de Anthropic (no hay credenciales disponibles para este agente, y no se debe gastar dinero real solo para medir). Son estimaciones por heurística de caracteres (`~4 caracteres ≈ 1 token`, la misma heurística que ya usa `anthropic_provider.py` vía `_APPROX_CHARS_PER_TOKEN`), calculadas sobre el contenido real de `prompts/*.txt` y `config/cvs.yaml`. Deben reemplazarse por medición real (`message.usage.input_tokens`/`output_tokens`) en cuanto exista logging de uso — ver `llm-cost-analysis.md`, recomendación (a).

## Configuración actual

```env
LLM_MAX_INPUT_TOKENS=12000
LLM_MAX_OUTPUT_TOKENS=1000
LLM_MAX_RETRIES=2
LLM_CACHE_ENABLED=true
```

| Variable | Valor | Justificación |
|---|---|---|
| `LLM_MAX_INPUT_TOKENS` | 12000 | Guardrail defensivo (`AnthropicProvider._max_input_chars = LLM_MAX_INPUT_TOKENS * 4`), no un presupuesto que se alcance en operación normal. LinkedIn limita sus posts a ~3000 caracteres (~750 tokens estimados) — muy por debajo de este techo de 12000 tokens/~48000 caracteres. En la práctica este límite nunca debería activarse; existe como defensa ante un post anómalamente largo, no como palanca de ahorro. **No confundir con** el truncado "de negocio" a ~800-1000 caracteres que sugiere `TOKEN_OPTIMIZATION.md` §3 — ese truncado NO está implementado todavía en el pipeline (ver `llm-cost-analysis.md`, recomendación (b)). |
| `LLM_MAX_OUTPUT_TOKENS` | 1000 | Techo compartido por ambas tareas. Estimado real de salida: Analyzer ≈ 40-90 tokens (JSON corto), Email Generator ≈ 90-180 tokens (subject+body) — el techo de 1000 nunca se alcanza en operación normal (Anthropic cobra por tokens generados, no por el techo configurado), así que hoy no genera costo extra, solo actúa como freno ante una generación descontrolada. Ver `llm-cost-analysis.md`, recomendación (d), para la sugerencia de separarlo por tarea. |
| `LLM_MAX_RETRIES` | 2 | Backoff exponencial acotado, delegado al propio SDK de Anthropic (`anthropic.Anthropic(max_retries=...)`) — nunca retry infinito, ver `TOKEN_OPTIMIZATION.md` §12. |
| `LLM_CACHE_ENABLED` | true | **Flag muerto hoy**: no leído por ningún código (verificado por grep en `app/**`, cero referencias). Reservado para prompt caching de Anthropic, pero implementarlo ahora no traería beneficio medible — ver detalle en `.env.example` y en `llm-cost-analysis.md`, recomendación (e). |

## Presupuesto por etapa del pipeline (estimado, heurística de caracteres — no medido contra la API real)

| Etapa | Modelo (env var) | Input tokens estimados/llamada | Output tokens estimados/llamada | Notas |
|---|---|---|---|---|
| Job Analyzer (`analyze_job`) | `ANTHROPIC_MODEL` (default `claude-3-5-haiku-latest`) | ~580-1250 (system prompt ~506 fijo + post 75-750 variable) | ~40-90 | Corre sobre todo lo que pasa el Candidate Filter (`is_candidate_job_post`) — mayor volumen del pipeline. |
| CV Matcher (fallback semántico) | no implementado | — | — | `SelectBestCV` es determinístico hoy (intersección de skills); no hay llamada LLM en este paso — no hay nada que presupuestar todavía. |
| Email Generator (`generate_email`) | `ANTHROPIC_EMAIL_MODEL` (default `claude-3-5-sonnet-latest`) | ~865-1600 (system prompt ~680 fijo + contexto/post/cv_summary variable) | ~90-180 | Corre solo sobre jobs en `CV_SELECTED` — menor volumen, prompt más largo, modelo de mejor calidad de redacción. |

Detalle del cálculo de cada fila en [`llm-cost-analysis.md`](./llm-cost-analysis.md), entrada 2026-09-16.

## Historial de cambios a este presupuesto

- **2026-09-16** — Primera medición (estimada) post-Fase 5. No se cambió ningún valor numérico de los límites (`LLM_MAX_INPUT_TOKENS`/`LLM_MAX_OUTPUT_TOKENS`/`LLM_MAX_RETRIES` quedan iguales — están correctamente dimensionados como guardrails, no como presupuestos ajustados). Se documentó que `LLM_CACHE_ENABLED` es un flag muerto (ver `.env.example` y `llm-cost-analysis.md`). Ver esa entrada para el detalle completo y las recomendaciones pendientes de medición real antes de tocar ningún límite.
