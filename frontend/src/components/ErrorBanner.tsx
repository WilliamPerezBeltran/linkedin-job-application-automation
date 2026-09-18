interface ErrorBannerProps {
  message: string;
  onDismiss?: () => void;
}

/**
 * Muestra un error legible para el usuario. `message` ya viene resuelto por
 * el caller (típicamente `ApiError.message`, que es el `detail` que la API
 * devuelve en JSON `{"detail": "..."}`) -- este componente no decide qué
 * significa el error, solo lo muestra.
 */
export function ErrorBanner({ message, onDismiss }: ErrorBannerProps) {
  return (
    <div role="alert" className="error-banner">
      <span>{message}</span>
      {onDismiss ? (
        <button type="button" onClick={onDismiss} aria-label="Dismiss error">
          &times;
        </button>
      ) : null}
    </div>
  );
}
