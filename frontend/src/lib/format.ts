/** Formatea un timestamp ISO 8601 (o `null`) para mostrar en la UI. */
export function formatDateTime(value: string | null): string {
  if (!value) {
    return "-";
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return parsed.toLocaleString();
}

/**
 * Devuelve `true` solo si `value` es una URL http(s) bien formada.
 *
 * `job.url`/`job.author` vienen de contenido scrapeado del feed de
 * LinkedIn (ver CLAUDE.md), no de una fuente confiable -- React ya escapa
 * el texto que renderiza (nunca se usa `dangerouslySetInnerHTML` en esta
 * app), pero un `href` con esquema `javascript:` igual sería peligroso si
 * el usuario hace click. Esta función es la guarda antes de renderizar
 * `job.url` como link clickeable en vez de texto plano.
 */
export function isSafeHttpUrl(value: string): boolean {
  try {
    const parsed = new URL(value);
    return parsed.protocol === "http:" || parsed.protocol === "https:";
  } catch {
    return false;
  }
}
