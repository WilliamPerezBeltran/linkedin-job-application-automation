# Optimización de tokens

Este proyecto llama a un LLM en al menos 3 puntos del pipeline (Job Analyzer, CV Matcher si se vuelve LLM-based, y Email Generator), y el scheduler puede correr diariamente sobre decenas de posts. Sin control, el costo escala rápido. Esta guía documenta dónde se gastan tokens en la app y cómo reducirlos sin perder calidad.

## 1. Dónde se gastan tokens en el pipeline

```
Job Analyzer      → 1 llamada por post scrapeado (la más frecuente)
Email Generator    → 1 llamada por job relevante (menos frecuente, pero prompt más largo)
```

El Analyzer corre sobre **todo** lo scrapeado (incluyendo ruido no-relevante); el Email Generator solo corre sobre lo ya filtrado. Por eso el Analyzer es el punto de mayor volumen y el primer lugar donde optimizar.

## 2. Filtrar antes de llamar al LLM

No mandar al LLM lo que se puede descartar con reglas simples y gratis.

- Mantener el filtro por keywords (`JOB_KEYWORDS`) **antes** del Analyzer. Si un post no contiene ninguna palabra relacionada con empleo/tech, ni se llama al LLM.
- Deduplicar por `content_hash` antes de analizar (ya está en el roadmap) — evita pagar dos veces por el mismo post repetido en el feed.
- Descartar posts triviales por longitud (`len(content) < 40`) sin gastar tokens.

```python
if not any(kw in content.lower() for kw in JOB_KEYWORDS):
    return  # nunca llega al LLM
```

Esto puede recortar el volumen de llamadas al Analyzer entre 60-90% dependiendo del feed.

## 3. Recortar el contenido de entrada

- Truncar el `content` del post a lo esencial antes de mandarlo (ej. primeros 800-1000 caracteres). Las ofertas rara vez necesitan el post completo para clasificar skills/categoría/email.
- Quitar hashtags repetidos, emojis decorativos, y firmas largas (`#hiring #java #remote #opportunity...` que LinkedIn suele acumular al final) con una limpieza regex simple antes de enviar.
- No mandar HTML crudo — el `parser.py` de la Fase 2 del roadmap ya debe normalizar a texto plano.

## 4. Prompts cortos y con salida estructurada (JSON mode / structured output)

- Usar el modo de salida estructurada (JSON schema) del proveedor en vez de pedir texto libre + parsear — evita que el modelo "explique" su respuesta y gaste tokens de salida innecesarios.
- Prompt de sistema fijo y corto, reutilizado igual en cada llamada (ver punto 5, prompt caching).
- Evitar few-shot examples largos en el prompt del Analyzer; con un schema bien definido y 1 ejemplo corto suele bastar.

```json
{"is_job": true, "category": "java", "seniority": "senior",
 "skills": ["Java","Spring Boot"], "email": "..."}
```

En vez de pedir explicaciones tipo "Explica por qué clasificaste así", que agregan tokens de salida sin valor para el pipeline.

## 5. Prompt caching

Si el proveedor lo soporta (Anthropic prompt caching / OpenAI cached input):

- Mantener el **system prompt** (instrucciones + schema + categorías permitidas) idéntico entre llamadas y marcarlo como cacheable — es la parte que se repite en cada uno de los N posts analizados por corrida.
- Solo el contenido del post (la parte variable) va fuera del bloque cacheado.
- Esto es especialmente rentable en el Analyzer, que corre en batch sobre múltiples posts con el mismo prompt base.

## 6. Elegir el modelo según la tarea

No usar el modelo más grande/caro para todo:

| Tarea | Complejidad | Modelo sugerido |
|---|---|---|
| Job Analyzer (clasificación, extracción de campos) | Baja-media, tarea estructurada | Modelo pequeño/rápido (ej. Haiku, GPT-4o-mini) |
| Email Generator (redacción, tono profesional) | Más exigente en calidad de texto | Modelo intermedio/grande (ej. Sonnet, GPT-4o) |

Correr el Analyzer con un modelo barato es la optimización de mayor impacto porque es la llamada de mayor volumen.

## 7. Reutilizar CVs, no reprocesarlos

- No mandar el PDF completo del CV al Email Generator en cada llamada. Mantener en `config/cvs.yaml` (o en una tabla) un **resumen corto ya redactado** de cada CV (3-5 líneas: rol, stack, años de experiencia) y mandar ese resumen, no el documento completo.
- El resumen se escribe una sola vez por CV, no se regenera por cada oferta.

## 8. Cachear resultados, no solo prompts

- Si el mismo `content_hash` ya fue analizado (`status != SCRAPED`), nunca reanalizar — esto ya está cubierto por la máquina de estados del roadmap, pero es la protección de tokens más importante: sin ella, un scraper que corre diario sobre un feed con posts repetidos vuelve a pagar por lo mismo cada día.
- Si dos posts distintos tienen contenido casi idéntico (ej. la misma vacante republicada por otra cuenta), considerar un hash aproximado (ej. shingling/simhash) más adelante para evitar reprocesar duplicados casi-exactos, no solo exactos.

## 9. Batching cuando aplique

- Si el proveedor soporta batch API (procesamiento asíncrono con descuento, ej. OpenAI Batch API / Anthropic Message Batches), correr el Analyzer del scheduler diario como batch en vez de N llamadas síncronas — suele tener costo reducido (~50%) a cambio de latencia mayor, lo cual es aceptable porque el scheduler ya corre de forma diferida (Fase 9 del roadmap).

## 10. Medir antes de optimizar más

- Loguear `input_tokens` / `output_tokens` por llamada (la mayoría de SDKs los devuelven en la respuesta) y guardarlos en la tabla `job_analysis` o en logs.
- Revisar semanalmente: cuántos tokens se gastaron, en qué etapa, y si el filtro de keywords realmente está reduciendo el volumen que llega al LLM.
- No optimizar a ciegas — el punto 2 (filtrar antes de llamar) suele dar más ahorro que cualquier ajuste fino de prompt.

## Dónde vive cada cosa

```
.claude/agents/14_token-optimization.md   → instrucciones del agente (no confundir con .agents/, que no usa Claude Code)
prompts/<feature>/vN.txt                  → prompts reales versionados que usa la app
docs/optimization/token-budget.md         → límites configurados (LLM_MAX_INPUT_TOKENS, etc.) y su justificación
docs/optimization/llm-cost-analysis.md    → mediciones antes/después de cada optimización (formato de la sección 20 de docs/agents/AGENTS.md)
```

`token-optimization-agent` (agente `14`) no implementa reglas de negocio — solo revisa y optimiza cómo los demás agentes (`llm-agent`, `cv-matching-agent`) usan el LLM, y documenta sus resultados en `docs/optimization/`.

## Resumen priorizado

1. Filtrar con keywords + dedup antes de tocar el LLM (mayor impacto, gratis).
2. Modelo barato para el Analyzer, modelo mejor solo para el Email Generator.
3. Prompt caching en el system prompt del Analyzer.
4. Resúmenes cortos de CV en vez de PDFs completos.
5. JSON estructurado en vez de texto libre.
6. Batch API para la corrida diaria del scheduler.
7. Medir tokens por etapa para saber dónde seguir optimizando.
