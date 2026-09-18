/**
 * Cliente HTTP delgado hacia `app/presentation/api/routes/jobs.py`
 * (Fase 6, ROADMAP.md). Deliberadamente `fetch` nativo envuelto en unas
 * pocas funciones -- este es un dashboard local de un solo usuario, traer
 * React Query/SWR/Redux sería sobre-ingeniería (YAGNI, ver
 * `docs/agents/AGENTS.md` principio 9).
 *
 * Usa rutas relativas ("/api/jobs...") a propósito: el backend
 * (`app/main.py`) no expone CORS, así que en desarrollo el proxy de Vite
 * (`vite.config.ts`) reenvía "/api/**" al backend FastAPI. Ver ese archivo
 * para el razonamiento completo.
 *
 * Ningún endpoint aquí decide transiciones de estado ni valida reglas de
 * negocio -- solo llama a la API y deja que el status code / el `detail`
 * de la respuesta indiquen qué pasó (p. ej. un 409 de "transición
 * inválida" se propaga tal cual para que la UI lo muestre, nunca se
 * anticipa del lado del cliente).
 */

import type {
  ApplicationResponse,
  DashboardStats,
  JobDetail,
  JobStatus,
  JobSummary,
} from "./types";

/** Error de la API, con el `detail` humano-legible que devuelve FastAPI. */
export class ApiError extends Error {
  /** `null` cuando el fetch ni siquiera llegó a completarse (red caída, backend apagado). */
  readonly status: number | null;

  constructor(message: string, status: number | null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

interface ErrorBody {
  detail?: unknown;
}

async function parseErrorDetail(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as ErrorBody;
    if (typeof body?.detail === "string" && body.detail.trim() !== "") {
      return body.detail;
    }
    // FastAPI/Pydantic devuelven `detail` como un array de objetos
    // {loc, msg, type} para un 422 nativo (p. ej. body malformado), a
    // diferencia del string plano que devuelven los `HTTPException`
    // deliberados del dominio (ver docstring de
    // `app/presentation/api/routes/jobs.py`). Ese caso no debería
    // ocurrir en uso normal (el cliente ya valida antes de enviar), pero
    // si ocurre, mostrar el detalle crudo es más útil que el mensaje
    // genérico de abajo.
    if (body?.detail !== undefined) {
      return JSON.stringify(body.detail);
    }
  } catch {
    // El cuerpo no era JSON (o estaba vacío) -- se usa el mensaje genérico.
  }
  return `Request failed with status ${response.status}.`;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      ...init,
      headers: { "Content-Type": "application/json", ...init?.headers },
    });
  } catch {
    throw new ApiError(
      "No se pudo contactar a la API. Verificá que el backend esté corriendo.",
      null,
    );
  }

  if (!response.ok) {
    throw new ApiError(await parseErrorDetail(response), response.status);
  }

  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

export function listJobs(
  jobStatus: JobStatus,
  limit: number,
  offset: number,
): Promise<JobSummary[]> {
  const params = new URLSearchParams({
    status: jobStatus,
    limit: String(limit),
    offset: String(offset),
  });
  return request<JobSummary[]>(`/api/jobs?${params.toString()}`);
}

export function getJob(id: string): Promise<JobDetail> {
  return request<JobDetail>(`/api/jobs/${encodeURIComponent(id)}`);
}

export function ignoreJob(id: string): Promise<JobSummary> {
  return request<JobSummary>(`/api/jobs/${encodeURIComponent(id)}/ignore`, {
    method: "POST",
  });
}

export function analyzeJob(id: string): Promise<JobDetail> {
  return request<JobDetail>(`/api/jobs/${encodeURIComponent(id)}/analyze`, {
    method: "POST",
  });
}

export function generateEmail(id: string): Promise<JobDetail> {
  return request<JobDetail>(
    `/api/jobs/${encodeURIComponent(id)}/generate-email`,
    { method: "POST" },
  );
}

export function editGeneratedEmail(
  id: string,
  subject: string,
  body: string,
): Promise<JobDetail> {
  return request<JobDetail>(`/api/jobs/${encodeURIComponent(id)}/email`, {
    method: "PATCH",
    body: JSON.stringify({ subject, body }),
  });
}

// Devuelve `ApplicationResponse`, no `JobDetail` -- a diferencia del resto de
// las acciones de este archivo, `POST .../create-draft` opera sobre un
// recurso distinto (`Application`, ver docstring de
// `app/application/dto/application_response.py`), no una vista actualizada
// del `Job`. Ver `JobDetail.tsx` para cómo se reconcilia esto con el estado
// local del componente.
export function createDraft(id: string): Promise<ApplicationResponse> {
  return request<ApplicationResponse>(
    `/api/jobs/${encodeURIComponent(id)}/create-draft`,
    { method: "POST" },
  );
}

// Confirmación manual de que el usuario ya envió el email desde Gmail (ver
// docstring de `app/presentation/api/routes/applications.py::mark_sent`) --
// nunca dispara un envío real, solo registra que ya ocurrió.
export function markApplicationSent(applicationId: string): Promise<ApplicationResponse> {
  return request<ApplicationResponse>(
    `/api/applications/${encodeURIComponent(applicationId)}/mark-sent`,
    { method: "POST" },
  );
}

export function getDashboardStats(): Promise<DashboardStats> {
  return request<DashboardStats>("/api/stats/dashboard");
}
