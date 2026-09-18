import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/client";
import type { JobSummary } from "../api/types";
import { JobList } from "./JobList";

vi.mock("../api/client", () => ({
  listJobs: vi.fn(),
  ignoreJob: vi.fn(),
  analyzeJob: vi.fn(),
  generateEmail: vi.fn(),
  ApiError: class ApiError extends Error {
    status: number | null;
    constructor(message: string, status: number | null) {
      super(message);
      this.status = status;
    }
  },
}));

import { analyzeJob, ignoreJob, listJobs } from "../api/client";

const RELEVANT_JOB: JobSummary = {
  id: "job-1",
  author: "Jane Recruiter",
  url: "https://linkedin.com/feed/update/1",
  status: "RELEVANT",
  published_at: "2026-09-01T10:00:00Z",
  scraped_at: "2026-09-01T10:05:00Z",
  job_type: "Python",
  skills: ["Python", "FastAPI"],
  recommended_cv: null,
};

describe("JobList", () => {
  beforeEach(() => {
    vi.mocked(listJobs).mockReset();
    vi.mocked(ignoreJob).mockReset();
    vi.mocked(analyzeJob).mockReset();
  });

  it("loads and renders jobs for the default status filter", async () => {
    vi.mocked(listJobs).mockResolvedValueOnce([RELEVANT_JOB]);

    render(<JobList onSelectJob={vi.fn()} />);

    expect(await screen.findByText("Jane Recruiter")).toBeInTheDocument();
    expect(listJobs).toHaveBeenCalledWith("RELEVANT", 20, 0);
  });

  it("shows the API error detail when loading fails", async () => {
    vi.mocked(listJobs).mockRejectedValueOnce(new ApiError("Boom", 500));

    render(<JobList onSelectJob={vi.fn()} />);

    expect(await screen.findByRole("alert")).toHaveTextContent("Boom");
  });

  it("calls onSelectJob when View is clicked", async () => {
    vi.mocked(listJobs).mockResolvedValueOnce([RELEVANT_JOB]);
    const onSelectJob = vi.fn();
    const user = userEvent.setup();

    render(<JobList onSelectJob={onSelectJob} />);
    await screen.findByText("Jane Recruiter");
    await user.click(screen.getByRole("button", { name: "View" }));

    expect(onSelectJob).toHaveBeenCalledWith("job-1");
  });

  it("ignores a job via the API and removes it once it no longer matches the filter", async () => {
    vi.mocked(listJobs).mockResolvedValueOnce([RELEVANT_JOB]);
    vi.mocked(ignoreJob).mockResolvedValueOnce({ ...RELEVANT_JOB, status: "IGNORED" });
    const user = userEvent.setup();

    render(<JobList onSelectJob={vi.fn()} />);
    await screen.findByText("Jane Recruiter");
    await user.click(screen.getByRole("button", { name: "Ignore" }));

    await waitFor(() => expect(ignoreJob).toHaveBeenCalledWith("job-1"));
    await waitFor(() => expect(screen.queryByText("Jane Recruiter")).not.toBeInTheDocument());
  });

  it("shows an action error (e.g. 409 conflict) without silently swallowing it", async () => {
    vi.mocked(listJobs).mockResolvedValueOnce([{ ...RELEVANT_JOB, status: "SCRAPED" }]);
    vi.mocked(analyzeJob).mockRejectedValueOnce(new ApiError("Job already analyzed.", 409));
    const user = userEvent.setup();

    render(<JobList onSelectJob={vi.fn()} />);
    await screen.findByText("Jane Recruiter");
    await user.click(screen.getByRole("button", { name: "Analyze" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Job already analyzed.");
  });
});
