import { useEffect, useState } from "react";
import { ApiError, getDashboardStats } from "../api/client";
import { JOB_STATUSES } from "../api/types";
import type { DashboardStats } from "../api/types";
import { ErrorBanner } from "./ErrorBanner";
import { Spinner } from "./Spinner";

/**
 * Tablero de histórico/trazabilidad (Fase 8, ROADMAP.md): un único fetch sin
 * parámetros a `GET /api/stats/dashboard` en el montaje. A diferencia de
 * `JobList`/`JobDetail`, no hay filtros ni un `jobId` que cambie durante la
 * vida del componente, así que no hace falta la guarda `latestRequestId`
 * contra respuestas "stale" que usan esos componentes (no hay forma de
 * disparar dos fetches en paralelo desde este componente).
 *
 * Solo tablas simples, sin librería de charting -- YAGNI para un dashboard
 * de un solo usuario (ver `docs/agents/AGENTS.md` principio 9). Todos los
 * números mostrados ya vienen agregados por el backend
 * (`app/presentation/api/routes/stats.py`); este componente no calcula ni
 * reordena nada, solo itera y renderiza.
 */
export function Dashboard() {
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function fetchStats() {
      setLoading(true);
      setError(null);
      try {
        const result = await getDashboardStats();
        if (!cancelled) {
          setStats(result);
        }
      } catch (err) {
        if (!cancelled) {
          setError(
            err instanceof ApiError ? err.message : "Unexpected error while loading the dashboard.",
          );
          setStats(null);
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }
    void fetchStats();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <section className="dashboard">
      <h1>Dashboard</h1>

      {loading ? <Spinner label="Loading dashboard..." /> : null}
      {error ? <ErrorBanner message={error} onDismiss={() => setError(null)} /> : null}

      {!loading && stats ? (
        <>
          <section>
            <h2>Jobs by status</h2>
            <table>
              <thead>
                <tr>
                  <th>Status</th>
                  <th>Count</th>
                </tr>
              </thead>
              <tbody>
                {JOB_STATUSES.map((jobStatus) => (
                  <tr key={jobStatus}>
                    <td>{jobStatus}</td>
                    <td>{stats.jobs_by_status[jobStatus] ?? 0}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>

          <section>
            <h2>Applications sent total</h2>
            <p className="dashboard-total">{stats.applications_sent_total}</p>
          </section>

          <section>
            <h2>Sent by week</h2>
            {stats.applications_sent_by_week.length > 0 ? (
              <table>
                <thead>
                  <tr>
                    <th>Week</th>
                    <th>Count</th>
                  </tr>
                </thead>
                <tbody>
                  {stats.applications_sent_by_week.map((entry) => (
                    <tr key={entry.week}>
                      <td>{entry.week}</td>
                      <td>{entry.count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <p>No applications sent yet.</p>
            )}
          </section>

          <section>
            <h2>Sent by category</h2>
            {stats.applications_sent_by_category.length > 0 ? (
              <table>
                <thead>
                  <tr>
                    <th>Category</th>
                    <th>Count</th>
                  </tr>
                </thead>
                <tbody>
                  {stats.applications_sent_by_category.map((entry) => (
                    <tr key={entry.category}>
                      <td>{entry.category}</td>
                      <td>{entry.count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <p>No applications sent yet.</p>
            )}
          </section>
        </>
      ) : null}
    </section>
  );
}
