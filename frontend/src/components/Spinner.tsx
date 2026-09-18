interface SpinnerProps {
  label?: string;
}

/** Indicador de carga simple, accesible via `role="status"`. */
export function Spinner({ label = "Loading..." }: SpinnerProps) {
  return (
    <div role="status" className="spinner">
      <span className="spinner-circle" aria-hidden="true" />
      <span>{label}</span>
    </div>
  );
}
