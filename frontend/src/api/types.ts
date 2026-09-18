/**
 * Tipos que reflejan los DTOs de la API (Fase 6, ROADMAP.md), definidos en
 * `app/application/dto/*` (backend, fuera del ownership de este agente).
 *
 * Deliberadamente un espejo 1:1 de esos DTOs -- este archivo NO agrega
 * campos ni lógica derivada (eso sería reimplementar reglas de negocio en
 * el cliente, prohibido para el Frontend Agent). Si el backend cambia un
 * contrato, este archivo debe actualizarse para reflejarlo, nunca al
 * revés.
 */

/** Espejo de `app.domain.value_objects.job_status.JobStatus` (StrEnum). */
export const JOB_STATUSES = [
  "SCRAPED",
  "ANALYZED",
  "RELEVANT",
  "NOT_RELEVANT",
  "CV_SELECTED",
  "EMAIL_GENERATED",
  "DRAFT_CREATED",
  "SENT",
  "IGNORED",
] as const;

export type JobStatus = (typeof JOB_STATUSES)[number];

/** Espejo de `app.application.dto.job_summary_response.JobSummaryResponse`. */
export interface JobSummary {
  id: string;
  author: string;
  url: string;
  status: JobStatus;
  published_at: string | null;
  scraped_at: string;
  job_type: string | null;
  skills: string[];
  recommended_cv: string | null;
}

/** Espejo de `app.application.dto.job_detail_response.JobDetailResponse`. */
export interface JobDetail extends JobSummary {
  seniority: string | null;
  languages: string[];
  frameworks: string[];
  cloud: string[];
  ai_related: boolean | null;
  matching_skills: string[];
  missing_skills: string[];
  subject: string | null;
  generated_email: string | null;
}

/** Espejo de `app.domain.value_objects.application_status.ApplicationStatus` (StrEnum). */
export type ApplicationStatus = "DRAFT_CREATED" | "SENT";

/** Espejo de `app.application.dto.application_response.ApplicationResponse`. */
export interface ApplicationResponse {
  id: string;
  job_id: string;
  email: string;
  subject: string;
  body: string;
  cv_path: string;
  gmail_draft_id: string | null;
  gmail_url: string | null;
  status: ApplicationStatus;
  sent_at: string | null;
}

/** Espejo de `app.application.dto.dashboard_stats_response.WeeklySentCount`. */
export interface WeeklySentCount {
  week: string;
  count: number;
}

/** Espejo de `app.application.dto.dashboard_stats_response.CategorySentCount`. */
export interface CategorySentCount {
  category: string;
  count: number;
}

/** Espejo de `app.application.dto.dashboard_stats_response.DashboardStatsResponse`. */
export interface DashboardStats {
  jobs_by_status: Record<string, number>;
  applications_sent_total: number;
  applications_sent_by_week: WeeklySentCount[];
  applications_sent_by_category: CategorySentCount[];
}
