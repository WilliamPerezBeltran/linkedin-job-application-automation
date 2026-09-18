# frontend

Dashboard de revisión manual (Fase 6, `ROADMAP.md`) para
`linkedin-job-application-automation`. React + TypeScript + Vite, sin
router ni librería de data-fetching -- ver `docs/agents/AGENTS.md` sección
14 (ownership del Frontend Agent) y los comentarios en `src/api/client.ts`
y `src/App.tsx` para el razonamiento de estas decisiones.

## Requisitos

* Node.js 20+ (probado con Node 24 / npm 11).
* El backend FastAPI corriendo (`uvicorn app.main:app --reload`, puerto
  `8000` por defecto) -- ver la raíz del repo.

## Correr en desarrollo

```bash
cd frontend
npm install
npm run dev
```

Abre `http://localhost:5173`. El servidor de desarrollo de Vite hace proxy
de `/api/**` hacia el backend (`vite.config.ts`), así que no hace falta
configurar CORS en FastAPI. Si el backend corre en otro host/puerto,
exportá `VITE_API_PROXY_TARGET` antes de `npm run dev` (por defecto
`http://localhost:8000`).

## Tests

```bash
npm run test        # una corrida (Vitest + Testing Library)
npm run test:watch  # modo watch
```

Todos los tests mockean `src/api/client.ts` -- nunca pegan contra un
backend real.

## Build de producción

```bash
npm run build     # tsc -b && vite build -> dist/
npm run preview   # sirve dist/ localmente
```

## Lint

```bash
npm run lint   # oxlint
```
