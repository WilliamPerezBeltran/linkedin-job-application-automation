import { useCallback, useEffect, useRef, useState } from "react";
import {
  analyzeJob,
  ApiError,
  createDraft,
  generateEmail,
  getJob,
  ignoreJob,
  markApplicationSent,
} from "../api/client";
import type { ApplicationResponse, JobDetail as JobDetailDto } from "../api/types";
import { formatDateTime, isSafeHttpUrl } from "../lib/format";
import { EmailEditor } from "./EmailEditor";
import { ErrorBanner } from "./ErrorBanner";
import { SkillMatch } from "./SkillMatch";
import { Spinner } from "./Spinner";

interface JobDetailProps {
  jobId: string;
  onBack: () => void;
}

/** Ver comentario equivalente en `JobList.tsx` -- misma ayuda de UX, no regla de negocio. */
function canIgnore(status: JobDetailDto["status"]): boolean {
  return !["NOT_RELEVANT", "DRAFT_CREATED", "SENT", "IGNORED"].includes(status);
}

export function JobDetail({ jobId, onBack }: JobDetailProps) {
  const [job, setJob] = useState<JobDetailDto | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionPending, setActionPending] = useState(false);
  // `POST .../create-draft` devuelve `ApplicationResponse`, un recurso
  // distinto de `JobDetailDto` (ver docstring de `createDraft` en
  // `api/client.ts`) -- se guarda aparte en vez de forzarlo dentro de
  // `job`. Solo se llena en esta sesión (ver limitación conocida más abajo,
  // en el JSX, para el caso de un `Job` que ya estaba en
  // `DRAFT_CREATED`/`SENT` al cargar la página).
  const [application, setApplication] = useState<ApplicationResponse | null>(null);

  // Ver comentario equivalente en `JobList.tsx` -- misma guarda contra
  // respuestas "stale" cuando se navega rápido entre jobs (hallazgo MEDIUM
  // de code-reviewer).
  const latestRequestId = useRef(0);

  const fetchJob = useCallback(async () => {
    const requestId = ++latestRequestId.current;
    setLoading(true);
    setError(null);
    // Se navega a otro `Job` (nuevo `jobId`) -- descarta cualquier
    // `Application` cargada para el job anterior, para no mostrar el link
    // "Open in Gmail" de un job distinto mientras carga el nuevo.
    setApplication(null);
    try {
      const result = await getJob(jobId);
      if (requestId === latestRequestId.current) {
        setJob(result);
      }
    } catch (err) {
      if (requestId === latestRequestId.current) {
        setError(err instanceof ApiError ? err.message : "Unexpected error while loading the job.");
        setJob(null);
      }
    } finally {
      if (requestId === latestRequestId.current) {
        setLoading(false);
      }
    }
  }, [jobId]);

  useEffect(() => {
    void fetchJob();
  }, [fetchJob]);

  async function runAction(action: () => Promise<JobDetailDto>) {
    setActionError(null);
    setActionPending(true);
    try {
      const updated = await action();
      setJob(updated);
    } catch (err) {
      setActionError(
        err instanceof ApiError ? err.message : "Unexpected error while updating the job.",
      );
    } finally {
      setActionPending(false);
    }
  }

  // `POST /{id}/ignore` responde `JobSummaryResponse`, no el detalle
  // completo (ver docstring de `jobs.py`) -- se combina con el `job`
  // actual para no perder los campos de detalle ya cargados (el resto de
  // esos campos no cambia al ignorar).
  async function handleIgnore() {
    if (!job) {
      return;
    }
    await runAction(async () => ({ ...job, ...(await ignoreJob(job.id)) }));
  }

  // No usa `runAction`: `createDraft` devuelve `ApplicationResponse`, no
  // `JobDetailDto` (son dos recursos HTTP distintos, ver docstring de
  // `application_response.py`), así que no encaja en la firma
  // `() => Promise<JobDetailDto>` que `runAction` espera. En vez de forzar
  // ese tipo, se replica acá el mismo manejo de `actionError`/
  // `actionPending` que `runAction` ya hace, y además se guarda la
  // `Application` devuelta y se refleja su `status` en el `job` local
  // (el backend ya hizo la transición real; esto solo la refleja en la UI).
  async function handleCreateDraft() {
    if (!job) {
      return;
    }
    setActionError(null);
    setActionPending(true);
    try {
      const result = await createDraft(job.id);
      setApplication(result);
      setJob({ ...job, status: result.status });
    } catch (err) {
      setActionError(
        err instanceof ApiError ? err.message : "Unexpected error while creating the draft.",
      );
    } finally {
      setActionPending(false);
    }
  }

  // Mismo criterio que `handleCreateDraft`: `markApplicationSent` devuelve
  // `ApplicationResponse`, no `JobDetailDto`, así que no encaja en
  // `runAction`. Solo puede llamarse cuando ya hay una `Application` cargada
  // en esta sesión (`applicationForJob`, ver la limitación conocida
  // documentada más abajo, en el JSX) -- no existe hoy un endpoint para
  // recuperar el `application_id` de un `Job` después de una recarga de
  // página, así que este botón comparte la misma deuda técnica que "Open in
  // Gmail".
  async function handleMarkSent() {
    if (!job || !applicationForJob) {
      return;
    }
    setActionError(null);
    setActionPending(true);
    try {
      const result = await markApplicationSent(applicationForJob.id);
      setApplication(result);
      setJob({ ...job, status: result.status });
    } catch (err) {
      setActionError(
        err instanceof ApiError ? err.message : "Unexpected error while marking the application as sent.",
      );
    } finally {
      setActionPending(false);
    }
  }

  // `application` guarda la respuesta de `createDraft` para *algún* job de
  // la sesión -- `application.job_id === job.id` confirma que corresponde al
  // job actualmente mostrado (guarda extra contra el `setApplication(null)`
  // de `fetchJob` llegando tarde por alguna carrera improbable).
  const draftAlreadyExists = job?.status === "DRAFT_CREATED" || job?.status === "SENT";
  const applicationForJob =
    application && job && application.job_id === job.id ? application : null;
  const gmailUrl = applicationForJob?.gmail_url ?? null;

  return (
    <section className="job-detail">
      <button type="button" onClick={onBack} className="back-link">
        &larr; Back to list
      </button>

      {loading ? <Spinner label="Loading job..." /> : null}
      {error ? <ErrorBanner message={error} onDismiss={() => setError(null)} /> : null}

      {!loading && job ? (
        <>
          <header>
            <h1>{job.author}</h1>
            {isSafeHttpUrl(job.url) ? (
              <a href={job.url} target="_blank" rel="noreferrer noopener">
                View original post
              </a>
            ) : (
              <span>{job.url}</span>
            )}
            <p>
              Status: <strong>{job.status}</strong> &middot; Scraped:{" "}
              {formatDateTime(job.scraped_at)} &middot; Published:{" "}
              {formatDateTime(job.published_at)}
            </p>
          </header>

          {actionError ? (
            <ErrorBanner message={actionError} onDismiss={() => setActionError(null)} />
          ) : null}

          <div className="job-actions">
            {job.status === "SCRAPED" ? (
              <button
                type="button"
                disabled={actionPending}
                onClick={() => void runAction(() => analyzeJob(job.id))}
              >
                Analyze
              </button>
            ) : null}
            {job.status === "CV_SELECTED" ? (
              <button
                type="button"
                disabled={actionPending}
                onClick={() => void runAction(() => generateEmail(job.id))}
              >
                Generate Email
              </button>
            ) : null}
            {canIgnore(job.status) ? (
              <button type="button" disabled={actionPending} onClick={() => void handleIgnore()}>
                Ignore
              </button>
            ) : null}
            {job.status === "EMAIL_GENERATED" ? (
              <button
                type="button"
                disabled={actionPending}
                onClick={() => void handleCreateDraft()}
              >
                Create Draft
              </button>
            ) : (
              <button
                type="button"
                disabled
                title={
                  draftAlreadyExists
                    ? "El draft de Gmail ya fue creado para este job."
                    : "Create Draft solo está disponible una vez que el email fue generado (status EMAIL_GENERATED)."
                }
              >
                Create Draft
              </button>
            )}
          </div>

          {gmailUrl && isSafeHttpUrl(gmailUrl) ? (
            <p>
              <a href={gmailUrl} target="_blank" rel="noreferrer noopener">
                Open in Gmail
              </a>
            </p>
          ) : draftAlreadyExists ? (
            // Limitación conocida (deuda técnica reportada al orchestrator):
            // `GET /api/jobs/{id}` (JobDetailResponse) no expone
            // `gmail_draft_id`/`gmail_url` -- ese dato solo vive en
            // `Application`, que no tiene endpoint de lectura por job hoy
            // (`GET /api/applications/by-job/{id}` no existe). Si el draft
            // ya fue creado en una sesión anterior (recarga de página, o se
            // abrió el job desde la lista después de crear el draft), no hay
            // forma de recuperar el link real acá -- solo se informa que ya
            // existe.
            <p>Draft already created for this job. Gmail link unavailable in this view.</p>
          ) : null}

          {applicationForJob?.status === "DRAFT_CREATED" ? (
            <div className="job-actions">
              <button type="button" disabled={actionPending} onClick={() => void handleMarkSent()}>
                Mark as Sent
              </button>
            </div>
          ) : draftAlreadyExists && job.status !== "SENT" ? (
            // Misma limitación conocida que "Open in Gmail" (ver comentario
            // arriba): sin una `Application` cargada en esta sesión (p. ej.
            // tras recargar la página) no hay `application_id` disponible
            // para llamar a `mark-sent` -- no se muestra el botón en ese
            // caso en vez de inventar un endpoint nuevo para resolverlo.
            <p>
              Mark as Sent unavailable in this view -- reopen this job right after creating the
              draft, in the same session.
            </p>
          ) : null}

          <section>
            <h2>Analysis</h2>
            {job.job_type ? (
              <dl className="analysis-grid">
                <dt>Category</dt>
                <dd>{job.job_type}</dd>
                <dt>Seniority</dt>
                <dd>{job.seniority ?? "-"}</dd>
                <dt>AI related</dt>
                <dd>{job.ai_related === null ? "-" : job.ai_related ? "Yes" : "No"}</dd>
                <dt>Skills</dt>
                <dd>{job.skills.length > 0 ? job.skills.join(", ") : "-"}</dd>
                <dt>Languages</dt>
                <dd>{job.languages.length > 0 ? job.languages.join(", ") : "-"}</dd>
                <dt>Frameworks</dt>
                <dd>{job.frameworks.length > 0 ? job.frameworks.join(", ") : "-"}</dd>
                <dt>Cloud</dt>
                <dd>{job.cloud.length > 0 ? job.cloud.join(", ") : "-"}</dd>
              </dl>
            ) : (
              <p>Not analyzed yet.</p>
            )}
          </section>

          <section>
            <h2>CV match</h2>
            <SkillMatch
              recommendedCv={job.recommended_cv}
              matchingSkills={job.matching_skills}
              missingSkills={job.missing_skills}
            />
          </section>

          <section>
            {job.subject !== null && job.generated_email !== null ? (
              <EmailEditor job={job} onSaved={setJob} />
            ) : (
              <p>No email generated yet.</p>
            )}
          </section>
        </>
      ) : null}
    </section>
  );
}
