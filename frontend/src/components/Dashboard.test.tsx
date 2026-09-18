import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/client";
import type { DashboardStats } from "../api/types";
import { Dashboard } from "./Dashboard";

vi.mock("../api/client", () => ({
  getDashboardStats: vi.fn(),
  ApiError: class ApiError extends Error {
    status: number | null;
    constructor(message: string, status: number | null) {
      super(message);
      this.status = status;
    }
  },
}));

import { getDashboardStats } from "../api/client";

const STATS: DashboardStats = {
  jobs_by_status: {
    SCRAPED: 3,
    ANALYZED: 1,
    RELEVANT: 0,
    NOT_RELEVANT: 2,
    CV_SELECTED: 1,
    EMAIL_GENERATED: 1,
    DRAFT_CREATED: 1,
    SENT: 2,
    IGNORED: 0,
  },
  applications_sent_total: 2,
  applications_sent_by_week: [
    { week: "2026-W37", count: 1 },
    { week: "2026-W38", count: 1 },
  ],
  applications_sent_by_category: [
    { category: "Python", count: 1 },
    { category: "unknown", count: 1 },
  ],
};

describe("Dashboard", () => {
  beforeEach(() => {
    vi.mocked(getDashboardStats).mockReset();
  });

  it("shows a loading spinner while the stats are being fetched", () => {
    vi.mocked(getDashboardStats).mockReturnValue(new Promise(() => {}));

    render(<Dashboard />);

    expect(screen.getByRole("status")).toBeInTheDocument();
  });

  it("renders jobs by status, total sent, sent by week and sent by category", async () => {
    vi.mocked(getDashboardStats).mockResolvedValueOnce(STATS);

    render(<Dashboard />);

    expect(await screen.findByRole("heading", { name: "Jobs by status" })).toBeInTheDocument();
    // 9 statuses, including ones with count 0.
    expect(screen.getByText("SCRAPED")).toBeInTheDocument();
    expect(screen.getByText("RELEVANT")).toBeInTheDocument();
    expect(screen.getByText("IGNORED")).toBeInTheDocument();

    expect(screen.getByRole("heading", { name: "Applications sent total" })).toBeInTheDocument();
    expect(document.querySelector(".dashboard-total")).toHaveTextContent("2");

    expect(screen.getByRole("heading", { name: "Sent by week" })).toBeInTheDocument();
    expect(screen.getByText("2026-W37")).toBeInTheDocument();
    expect(screen.getByText("2026-W38")).toBeInTheDocument();

    expect(screen.getByRole("heading", { name: "Sent by category" })).toBeInTheDocument();
    expect(screen.getByText("Python")).toBeInTheDocument();
    expect(screen.getByText("unknown")).toBeInTheDocument();
  });

  it("shows the API error via ErrorBanner when the fetch fails", async () => {
    vi.mocked(getDashboardStats).mockRejectedValueOnce(
      new ApiError("No se pudo contactar a la API. Verificá que el backend esté corriendo.", null),
    );

    render(<Dashboard />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "No se pudo contactar a la API. Verificá que el backend esté corriendo.",
    );
  });
});
