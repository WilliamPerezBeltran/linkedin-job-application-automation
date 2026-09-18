import { useEffect, useState } from "react";
import { ApiError, editGeneratedEmail } from "../api/client";
import type { JobDetail } from "../api/types";
import { ErrorBanner } from "./ErrorBanner";

interface EmailEditorProps {
  job: JobDetail;
  onSaved: (updated: JobDetail) => void;
}

/**
 * Preview editable de subject/body. El guardado SIEMPRE pega a
 * `PATCH /api/jobs/{id}/email` (`editGeneratedEmail`) -- nunca queda solo en
 * el estado local del componente, tal como exige ROADMAP.md Fase 6 punto 3.
 * `subject`/`body` locales son un borrador de edición hasta que "Save
 * changes" confirma contra el servidor.
 */
export function EmailEditor({ job, onSaved }: EmailEditorProps) {
  const [subject, setSubject] = useState(job.subject ?? "");
  const [body, setBody] = useState(job.generated_email ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedAt, setSavedAt] = useState<number | null>(null);

  // Solo se resetea el borrador cuando cambia de job (navegación a otro
  // job.id) -- evita mostrar el borrador editado de un job anterior.
  // Deliberadamente NO depende de job.subject/job.generated_email: un
  // guardado exitoso hace que el padre (JobDetail) actualice esos mismos
  // campos para que coincidan con el borrador local ya escrito acá, y si
  // este efecto reaccionara a ese cambio borraría `savedAt` en el mismo
  // ciclo en el que se acaba de confirmar el guardado, ocultando la
  // confirmación de "Changes saved." antes de que el usuario la vea.
  useEffect(() => {
    setSubject(job.subject ?? "");
    setBody(job.generated_email ?? "");
    setSavedAt(null);
    setError(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job.id]);

  const dirty = subject !== (job.subject ?? "") || body !== (job.generated_email ?? "");

  async function handleSave() {
    setSaving(true);
    setError(null);
    setSavedAt(null);
    try {
      const updated = await editGeneratedEmail(job.id, subject, body);
      onSaved(updated);
      setSavedAt(Date.now());
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "Unexpected error while saving the email.",
      );
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="email-editor">
      <h2>Email preview</h2>
      {error ? <ErrorBanner message={error} onDismiss={() => setError(null)} /> : null}
      {savedAt && !dirty ? (
        <p role="status" className="save-confirmation">
          Changes saved.
        </p>
      ) : null}

      <label className="email-field">
        Subject
        <input
          type="text"
          value={subject}
          onChange={(event) => setSubject(event.target.value)}
          disabled={saving}
        />
      </label>

      <label className="email-field">
        Body
        <textarea
          value={body}
          rows={12}
          onChange={(event) => setBody(event.target.value)}
          disabled={saving}
        />
      </label>

      <button
        type="button"
        onClick={() => void handleSave()}
        disabled={saving || !dirty || subject.trim() === "" || body.trim() === ""}
      >
        {saving ? "Saving..." : "Save changes"}
      </button>
    </div>
  );
}
