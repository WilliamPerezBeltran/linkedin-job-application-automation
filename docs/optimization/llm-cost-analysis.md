# LLM Cost Analysis

Registro histórico de mediciones y optimizaciones de costo/latencia de LLM en Job Application Automation. Cada optimización aplicada por `token-optimization-agent` (ver [`.claude/agents/14_token-optimization.md`](../../.claude/agents/14_token-optimization.md)) se documenta aquí con el formato de reporte definido en ese archivo y en `docs/agents/AGENTS.md` §36.

Estado: Fases 3 y 5 del [`ROADMAP.md`](../../ROADMAP.md) ya implementadas (`app/infrastructure/llm/anthropic_provider.py`: `analyze_job` y `generate_email`). Primera entrada agregada 2026-09-16.

## Formato de cada entrada

```
## YYYY-MM-DD — <título de la optimización>

### Problema
<qué estaba consumiendo tokens>

### Estado actual
<input/output tokens, llamadas por job, costo>

### Cambio
<qué se cambió>

### Impacto esperado
<reducción estimada>

### Impacto medido
<reducción real>

### Impacto en calidad
<ninguno / impacto medido>

### Tests
<tests ejecutados>

### Riesgos
<riesgos restantes>

### Recomendación
<siguiente optimización, si aplica>
```

## Entradas

## 2026-09-16 — Primera revisión transversal post-Fase 5 (Job Analyzer + Email Generator)

**Método de medición:** sin acceso a credenciales de Anthropic ni autorización para gastar dinero real en esta tarea — todos los números de esta entrada son **estimados** con la heurística de ~4 caracteres/token (la misma que ya usa `anthropic_provider.py` vía `_APPROX_CHARS_PER_TOKEN`) aplicada al contenido real de `prompts/job-analysis/v1.txt` (2024 caracteres), `prompts/email-generation/v1.txt` (2719 caracteres) y `config/cvs.yaml` (summaries de 185-285 caracteres). No reemplazan una medición real de `message.usage` — ver recomendación (a).

### Problema
Cierre de Fase 5: primer punto en el roadmap donde existen las dos llamadas LLM reales del pipeline (`AnthropicProvider.analyze_job`, `AnthropicProvider.generate_email`) y corresponde la primera auditoría de costo/eficiencia transversal.

### Estado actual

```
Input tokens estimados actuales:
  - Job Analyzer (analyze_job): ~580-1250 tokens/llamada
    (system prompt fijo ~506 tokens + post variable ~75-750 tokens;
    el techo defensivo de 12000 tokens/~48000 caracteres nunca se alcanza
    en la práctica porque LinkedIn limita sus posts a ~3000 caracteres).
  - Email Generator (generate_email): ~865-1600 tokens/llamada
    (system prompt fijo ~680 tokens + contexto estructurado ~65-100 tokens
    + post variable ~75-750 tokens + cv_summary ~45-70 tokens).

Output tokens estimados actuales:
  - Job Analyzer: ~40-90 tokens/llamada (JSON corto, arrays pequeños).
  - Email Generator: ~90-180 tokens/llamada (subject + body en JSON).
  Ambos muy por debajo del techo compartido LLM_MAX_OUTPUT_TOKENS=1000.

Llamadas promedio por job:
  - Analyzer: 1 llamada por Job en SCRAPED que pase is_candidate_job_post
    (candidate_filter ya descarta el resto sin costo, TOKEN_OPTIMIZATION.md §2).
  - Email Generator: 1 llamada por Job en CV_SELECTED — subconjunto bastante
    menor que el total de SCRAPED (solo lo clasificado is_job=true y con CV
    matcheado). No hay llamada LLM en CV Matcher (SelectBestCV es
    determinístico, sin fallback semántico implementado todavía).

Fuente principal de consumo:
  El volumen de llamadas al Job Analyzer (corre sobre todo lo que sobrevive
  al candidate filter), no el tamaño de cada prompt individual — ambos
  prompts ya son cortos y sin relleno.

Oportunidad principal de optimización:
  No hay ninguna de aplicación inmediata y segura sin medición real (ver
  recomendaciones abajo). La pieza que más desbloquea futuras optimizaciones
  es (a) instrumentar input_tokens/output_tokens reales por llamada — hoy no
  se loguean en absoluto, así que cualquier ajuste posterior de límites o
  prompts estaría, otra vez, basado en estimación en vez de datos reales.

Impacto esperado:
  Ninguno todavía en esta entrada — es una auditoría, no un cambio de
  comportamiento del pipeline.

Riesgo:
  Bajo para lo aplicado en esta revisión (ver "Cambio" abajo, solo
  documentación/comentarios). Las oportunidades con riesgo real (truncado
  de contenido, stripping de hashtags, límites de output por tarea) quedan
  explícitamente sin aplicar — ver "Riesgos" y "Recomendación".
```

### Qué ya está bien hecho (no tocado, por diseño)

- **Candidate filter determinístico antes del Analyzer** (`app/application/services/candidate_filter.py`, `is_candidate_job_post`): descarta por longitud (`< 40` caracteres) y por ausencia de keywords de empleo/tech antes de gastar un solo token, exactamente como pide `TOKEN_OPTIMIZATION.md` §2. Vive en `application/services/` (no en `infrastructure/llm/`), respetando el dependency rule — ya corregido en un code review previo, no hay nada que tocar.
- **Deduplicación real**: `content_hash` único + máquina de estados (`SCRAPED → ANALYZED/RELEVANT/NOT_RELEVANT`) garantizan que un `Job` nunca se re-analiza ni se le regenera el email dos veces (`AnalyzeJobPost`/`GenerateApplicationEmail` solo listan por `status`). Cubre `TOKEN_OPTIMIZATION.md` §8.
- **Modelo por tarea, no un único modelo para todo**: `ANTHROPIC_MODEL` (Haiku, alto volumen) para `analyze_job` vs `ANTHROPIC_EMAIL_MODEL` (Sonnet, mejor calidad de redacción) para `generate_email`, ambos overrideables por env var — implementa `TOKEN_OPTIMIZATION.md` §6 al pie de la letra.
- **Salida estructurada mínima**: ambos prompts piden JSON exclusivo, prohíben explícitamente markdown/explicaciones, y no piden chain-of-thought. Los ejemplos dentro del prompt son únicos y cortos (no few-shot extenso). Cubre §4.
- **Resumen corto de CV, no el PDF completo**: `config/cvs.yaml` ya tiene un campo `summary` de 185-285 caracteres (~46-71 tokens estimados) por CV, escrito una sola vez, reutilizado en cada llamada vía `CVCatalog.get_summary()` — implementa §7 exactamente como está documentado, con tamaños reales confirmados.
- **Higiene de logs**: ningún path de error (`AnthropicProvider._complete`, `_parse_and_validate_analysis`, `_parse_and_validate_email`, y los `except` en `AnalyzeJobPost`/`GenerateApplicationEmail`) loguea `content`/`raw_text`/el email generado — solo `job_id` y `type(exc).__name__`. Correcto per `docs/agents/AGENTS.md` §24.
- **Retries acotados**: delegados al propio SDK (`anthropic.Anthropic(max_retries=LLM_MAX_RETRIES)`), sin loop propio, nunca infinito. Cubre §12.
- **Truncado defensivo de input** (`_max_input_chars` derivado de `LLM_MAX_INPUT_TOKENS`) aplicado de forma consistente a `content`/`job_content`/`cv_summary` en ambos métodos — capa de seguridad razonable, aunque en la práctica nunca se activa (ver abajo).

### Cambio

Solo documentación/comentarios, sin tocar `app/infrastructure/llm/**` ni `prompts/**`:

1. `.env.example`: agregado comentario junto a `LLM_CACHE_ENABLED=true` marcándolo explícitamente como **flag muerto** — confirmado por grep (`grep -rn "LLM_CACHE_ENABLED" --include="*.py" .` → cero resultados en todo `app/**`) que ningún código lo lee. El comentario explica además por qué implementarlo hoy no traería beneficio: los system prompts actuales (~506 y ~680 tokens estimados) están por debajo del mínimo cacheable de Anthropic (1024 tokens en general, 2048 para la familia Haiku usada en el Analyzer) — cachear un prompt que no llega al mínimo no genera ningún ahorro, solo agrega complejidad de formato de request (`cache_control`, posibles headers beta según versión de SDK). Esto es exactamente el escenario que el guardrail "no agregar complejidad al prompt sin beneficio medible" prohíbe.
2. `docs/optimization/token-budget.md`: reemplazados los placeholders "a completar con datos reales" por las estimaciones de esta entrada, con la justificación real de cada límite (por qué `LLM_MAX_INPUT_TOKENS`/`LLM_MAX_OUTPUT_TOKENS` son guardrails que hoy no se activan, no presupuestos ajustados a la baja).
3. Este archivo (`llm-cost-analysis.md`): primera entrada real.

No se modificó ningún valor de configuración (`LLM_MAX_INPUT_TOKENS`, `LLM_MAX_OUTPUT_TOKENS`, `LLM_MAX_RETRIES` quedan en 12000/1000/2), ni `anthropic_provider.py`, ni ningún prompt.

### Impacto esperado

Ninguno en tokens/costo (no se cambió comportamiento). El impacto es de mantenibilidad: elimina un flag de configuración engañoso y dota a `token-budget.md`/`llm-cost-analysis.md` de una primera línea de base real (aunque estimada) para comparar contra mediciones futuras.

### Impacto medido

N/A — cambio de documentación/comentarios únicamente, sin ejecución de código afectada.

### Impacto en calidad

Ninguno — no se tocó ningún prompt, modelo, ni lógica de filtrado/truncado.

### Tests

No aplica ejecución de `pytest`/`ruff`/`mypy` para esta entrada: los únicos archivos modificados son `.env.example` (no es código, no se importa) y los dos `.md` de `docs/optimization/`. Ninguno de los tres es ejecutado ni importado por la suite de tests. Se corrió igualmente `grep -rn "LLM_CACHE_ENABLED" --include="*.py" .` sobre todo el repo para confirmar el hallazgo de "flag muerto" antes de documentarlo (cero resultados).

### Riesgos

Ninguno introducido por esta entrada. Riesgos identificados pero **no resueltos** (ver recomendaciones (b) y (c) abajo): cualquier truncado más agresivo del contenido del post o limpieza de hashtags podría eliminar la única mención de un email de contacto o una skill/tecnología, y no debe aplicarse sin una medición real o al menos un set de fixtures reales de posts de LinkedIn que lo valide.

### Recomendación

No aplicadas en esta pasada por requerir medición real contra la API de Anthropic, cambio de comportamiento observable, o coordinación explícita con `llm-agent`/`backend-engineer` — quedan documentadas como próximos pasos, en orden de prioridad:

**(a) [HIGH] Instrumentar `input_tokens`/`output_tokens`/latencia reales por llamada.**
Hoy `_AnthropicSDKClient.complete()` (`app/infrastructure/llm/anthropic_provider.py`) descarta `message.usage` por completo — solo devuelve el texto concatenado. Sin esto, todo el resto de esta página (y cualquier ajuste futuro de límites/prompts) sigue basado en estimación, nunca en datos reales, violando `TOKEN_OPTIMIZATION.md` §10 ("medir antes de optimizar más") y el requisito de observabilidad de `docs/agents/AGENTS.md` §13/§24 (`input_tokens, output_tokens, total_tokens, estimated_cost, latency_ms, ...`). Recomendación concreta: cambiar el retorno de `_CompletionClient.complete` (Protocol interno, privado de este módulo) de `str` a una estructura pequeña (texto + `input_tokens` + `output_tokens`) y loguear esos valores (nunca el contenido) junto con `operation` (`"analyze_job"`/`"generate_email"`), `model` y `prompt_version`. No aplicado ahora porque: (1) cambia el shape del seam interno usado por los dobles de test existentes en `tests/`, lo que corresponde coordinar con `llm-agent`/`testing-agent`; (2) `job_id` no está disponible dentro de `AnthropicProvider` (la interfaz `LLMProvider.analyze_job(content: str)`/`generate_email(context)` de ADR-005 no lo recibe), así que loguear con `job_id` correlacionado requeriría tocar la interfaz compartida — cambio de interfaz, no algo que este agente deba decidir unilateralmente.

**(b) [MEDIUM] Truncado "de negocio" del post a ~800-1000 caracteres antes del Analyzer (`TOKEN_OPTIMIZATION.md` §3).**
No implementado — el único truncado que existe hoy es el defensivo de `LLM_MAX_INPUT_TOKENS` (~48000 caracteres), que nunca se activa porque LinkedIn ya limita sus posts a ~3000 caracteres. Un truncado a 800-1000 caracteres ahorraría algo de input tokens en posts largos, pero arriesga cortar el email de contacto o una skill mencionada al final del post (muchos posts ponen el "escribinos a..." al cierre) — el Analyzer depende de esa cola del texto. No aplicar sin antes: (1) juntar una muestra real/fixture de posts de LinkedIn de la categoría objetivo, (2) confirmar dónde suele aparecer el email/las skills clave en esa muestra, (3) medir con `pytest` de regresión semántica que el truncado propuesto no pierde ningún email/skill de la muestra.

**(c) [LOW] Limpieza de hashtags/firmas decorativas (`TOKEN_OPTIMIZATION.md` §3).**
No implementado. Riesgo: algunos posts solo mencionan una tecnología como hashtag final (`#Kubernetes`), y el Analyzer no debe "inventar" ni perder skills reales. Si se implementa, debe ser con una lista explícita de hashtags genéricos a remover (`#hiring #remote #vacante #oportunidad`, etc.), nunca un regex que borre "todo hashtag al final del texto" — y validado contra fixtures reales antes de activarse.

**(d) [LOW] Separar `LLM_MAX_OUTPUT_TOKENS` por tarea.**
Hoy es un único valor (1000) compartido entre Analyzer (~40-90 tokens reales estimados) y Email Generator (~90-180). No genera costo extra hoy (Anthropic cobra por tokens generados, no por el techo), pero un techo específico y más ajustado para el Analyzer serviría como mejor guardrail ante una generación descontrolada. No se ajusta ahora porque bajar el techo sin datos reales de (a) arriesga truncar una respuesta legítima (post con muchas skills) a mitad del JSON, lo que rompería `json.loads` y contaría como `failed_llm_calls` — hacerlo bien requiere primero (a).

**(e) [LOW, diferido a propósito] Prompt caching de Anthropic (`TOKEN_OPTIMIZATION.md` §5).**
Ver "Cambio" arriba — los prompts actuales no alcanzan el mínimo cacheable de Anthropic. Revisar de nuevo solo si el system prompt crece sustancialmente o si se justifica reestructurar la llamada (ej. agregar varios ejemplos few-shot compartidos). Documentado, no una tarea pendiente activa.

**(f) [Confirmado como no aplicable todavía] Batch API (`TOKEN_OPTIMIZATION.md` §9).**
`app/presentation/scheduler/` existe pero está vacío (`__init__.py` sin contenido, Fase 9 del roadmap sin implementar). Revisitar cuando el scheduler diario exista — es el momento natural para evaluar Anthropic Message Batches en el Analyzer.
