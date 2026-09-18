import { JOB_STATUSES, type JobStatus } from "../api/types";

interface StatusSelectProps {
  value: JobStatus;
  onChange: (status: JobStatus) => void;
  disabled?: boolean;
}

/** Dropdown con los 9 valores válidos de `JobStatus` -- ver `api/types.ts`. */
export function StatusSelect({ value, onChange, disabled }: StatusSelectProps) {
  return (
    <label className="status-select">
      Status
      <select
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value as JobStatus)}
      >
        {JOB_STATUSES.map((status) => (
          <option key={status} value={status}>
            {status}
          </option>
        ))}
      </select>
    </label>
  );
}
