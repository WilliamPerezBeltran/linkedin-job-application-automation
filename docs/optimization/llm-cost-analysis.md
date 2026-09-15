# LLM Cost Analysis

Registro histórico de mediciones y optimizaciones de costo/latencia de LLM en Job Application Automation. Cada optimización aplicada por `token-optimization-agent` (ver [`.claude/agents/14_token-optimization.md`](../../.claude/agents/14_token-optimization.md)) se documenta aquí con el formato de reporte definido en ese archivo y en `docs/agents/AGENTS.md` §36.

Estado: sin entradas todavía — el pipeline de LLM no está implementado (ver [`ROADMAP.md`](../../ROADMAP.md) Fases 3 y 5). Este archivo empieza a llenarse una vez haya llamadas reales al LLM que medir.

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

_(ninguna todavía)_
