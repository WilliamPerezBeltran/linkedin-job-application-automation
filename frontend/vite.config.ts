/// <reference types="vitest/config" />
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// La API FastAPI (app/main.py) no expone CORS (ver
// app/presentation/api/**) -- este es un dashboard local de un solo
// usuario (CLAUDE.md), así que en vez de pedirle al backend-engineer que
// agregue CORS solo para el dev server, se usa el proxy de Vite: todo lo
// que el cliente pide a rutas relativas "/api/..." se reenvía al backend
// FastAPI, que corre en el mismo origen desde el punto de vista del
// navegador. VITE_API_PROXY_TARGET permite apuntar a otro host/puerto si
// hace falta (ver frontend/.env.example).
const apiProxyTarget = process.env.VITE_API_PROXY_TARGET ?? 'http://localhost:8000'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': { target: apiProxyTarget, changeOrigin: true },
    },
  },
  preview: {
    proxy: {
      '/api': { target: apiProxyTarget, changeOrigin: true },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
  },
})
