import { useCallback, useEffect, useRef, useState } from "react";
import { analyzeJob, ApiError, generateEmail, ignoreJob, listJobs } from "../api/client";
import type { JobStatus, JobSummary } from "../api/types";
import { formatDateTime } from "../lib/format";
import { ErrorBanner } from "./ErrorBanner";
import { Spinner } from "./Spinner";
import { StatusSelect } from "./StatusSelect";

const PAGE_SIZE = 20;

/**
 * Ayuda de UX, no regla de negocio: la API es la única que decide si una
 * transición es válida (responde 409 si no lo es, y ese 409 se muestra tal
 * cual). Esto solo evita mostrar botones obviamente no aplicables según el
 * `status` que la propia API ya devolvió -- ver
 * `app/domain/value_objects/job_status.py` para la máquina de estados real
 * que vive en el backend.
 */
function canIgnore(status: JobStatus): boolean {
  return !["NOT_RELEVANT", "DRAFT_CREATED", "SENT", "IGNORED"].includes(status);
}

interface JobListProps {
  onSelectJob: (jobId: string) => void;
}

export function JobList({ onSelectJob }: JobListProps) {
  const [status, setStatus] = useState<JobStatus>("RELEVANT");
  const [offset, setOffset] = useState(0);
  const [jobs, setJobs] = useState<JobSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [pendingActionId, setPendingActionId] = useState<string | null>(null);

  // Guarda contra respuestas "stale": si el usuario cambia el filtro de
  // status (o la paginación) dos veces rápido, una request anterior podría
  // resolver después de la más reciente y pisar el estado con datos
  // viejos (hallazgo MEDIUM de code-reviewer). Cada fetch se etiqueta con
  // un id incremental; solo el fetch cuyo id sigue siendo el más reciente
  // al resolver aplica su resultado.
  const latestRequestId = useRef(0);

  const fetchJobs = useCallback(async () => {
    const requestId = ++latestRequestId.current;
    setLoading(true);
    setError(null);
    try {
      const result = await listJobs(status, PAGE_SIZE, offset);
      if (requestId === latestRequestId.current) {
        setJobs(result);
      }
    } catch (err) {
      if (requestId === latestRequestId.current) {
        setError(err instanceof ApiError ? err.message : "Unexpected error while loading jobs.");
        setJobs([]);
      }
    } finally {
      if (requestId === latestRequestId.current) {
        setLoading(false);
      }
    }
  }, [status, offset]);

  useEffect(() => {
    void fetchJobs();
  }, [fetchJobs]);

  function handleStatusChange(nextStatus: JobStatus) {
    setStatus(nextStatus);
    setOffset(0);
  }

  async function runAction(jobId: string, action: () => Promise<JobSummary>) {
    setActionError(null);
    setPendingActionId(jobId);
    try {
      const updated = await action();
      // El job puede haber salido del filtro actual (p. ej. Analyze mueve
      // SCRAPED -> RELEVANT) -- si ya no matchea el status filtrado, se
      // quita de la lista en vez de mostrar un dato inconsistente.
      setJobs((current) =>
        updated.status === status
          ? current.map((job) => (job.id === jobId ? updated : job))
          : current.filter((job) => job.id !== jobId),
      );
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Unexpected error while updating the job.");
    } finally {
      setPendingActionId(null);
    }
  }

  return (
    <section>
      <header className="job-list-header">
        <h1>Jobs</h1>
        <StatusSelect value={status} onChange={handleStatusChange} disabled={loading} />
      </header>

      {actionError ? (
        <ErrorBanner message={actionError} onDismiss={() => setActionError(null)} />
      ) : null}

      {loading ? <Spinner label="Loading jobs..." /> : null}
      {error ? <ErrorBanner message={error} onDismiss={() => setError(null)} /> : null}

      {!loading && !error && jobs.length === 0 ? (
        <p>No jobs with status {status}.</p>
      ) : null}

      {!loading && jobs.length > 0 ? (
        <table className="job-table">
          <thead>
            <tr>
              <th>Author</th>
              <th>Category</th>
              <th>Skills</th>
              <th>Recommended CV</th>
              <th>Status</th>
              <th>Scraped</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {jobs.map((job) => (
              <tr key={job.id}>
                <td>{job.author}</td>
                <td>{job.job_type ?? "-"}</td>
                <td>{job.skills.length > 0 ? job.skills.join(", ") : "-"}</td>
                <td>{job.recommended_cv ?? "-"}</td>
                <td>{job.status}</td>
                <td>{formatDateTime(job.scraped_at)}</td>
                <td className="job-actions">
                  <button type="button" onClick={() => onSelectJob(job.id)}>
                    View
                  </button>
                  {job.status === "SCRAPED" ? (
                    <button
                      type="button"
                      disabled={pendingActionId === job.id}
                      onClick={() => void runAction(job.id, () => analyzeJob(job.id))}
                    >
                      Analyze
                    </button>
                  ) : null}
                  {job.status === "CV_SELECTED" ? (
                    <button
                      type="button"
                      disabled={pendingActionId === job.id}
                      onClick={() => void runAction(job.id, () => generateEmail(job.id))}
                    >
                      Generate Email
                    </button>
                  ) : null}
                  {canIgnore(job.status) ? (
                    <button
                      type="button"
                      disabled={pendingActionId === job.id}
                      onClick={() => void runAction(job.id, () => ignoreJob(job.id))}
                    >
                      Ignore
                    </button>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}

      <footer className="job-list-pagination">
        <button type="button" disabled={loading || offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
          Previous
        </button>
        <span>Offset {offset}</span>
        <button type="button" disabled={loading || jobs.length < PAGE_SIZE} onClick={() => setOffset(offset + PAGE_SIZE)}>
          Next
        </button>
      </footer>
    </section>
  );
}
