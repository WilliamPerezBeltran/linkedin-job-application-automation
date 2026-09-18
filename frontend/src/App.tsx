import { useState } from "react";
import { Dashboard } from "./components/Dashboard";
import { JobDetail } from "./components/JobDetail";
import { JobList } from "./components/JobList";

type Screen = "jobs" | "dashboard";

/**
 * Composition root de la UI. Router mínimo manual (`useState`) en vez de
 * `react-router-dom` -- un dashboard local de un solo usuario con un puñado
 * de pantallas (lista/detalle de jobs, y ahora el tablero de histórico) no
 * justifica esa dependencia (YAGNI). `screen` decide entre el flujo de Jobs
 * (lista + detalle, con su propio sub-estado `selectedJobId`) y el
 * `Dashboard`; `selectedJobId` no se resetea al cambiar de pantalla para
 * volver directo al job que se estaba viendo si el usuario vuelve a "Jobs".
 */
function App() {
  const [screen, setScreen] = useState<Screen>("jobs");
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);

  return (
    <main className="app">
      <nav className="app-nav">
        <button
          type="button"
          aria-current={screen === "jobs" ? "page" : undefined}
          onClick={() => setScreen("jobs")}
        >
          Jobs
        </button>
        <button
          type="button"
          aria-current={screen === "dashboard" ? "page" : undefined}
          onClick={() => setScreen("dashboard")}
        >
          Dashboard
        </button>
      </nav>

      {screen === "dashboard" ? <Dashboard /> : null}

      {screen === "jobs" ? (
        selectedJobId ? (
          <JobDetail jobId={selectedJobId} onBack={() => setSelectedJobId(null)} />
        ) : (
          <JobList onSelectJob={setSelectedJobId} />
        )
      ) : null}
    </main>
  );
}

export default App;
