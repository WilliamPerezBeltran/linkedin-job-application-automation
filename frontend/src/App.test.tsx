import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { JobDetail as JobDetailDto, JobSummary } from "./api/types";
import App from "./App";

vi.mock("./api/client", () => ({
  listJobs: vi.fn(),
  getJob: vi.fn(),
  ignoreJob: vi.fn(),
  analyzeJob: vi.fn(),
  generateEmail: vi.fn(),
  editGeneratedEmail: vi.fn(),
  ApiError: class ApiError extends Error {
    status: number | null;
    constructor(message: string, status: number | null) {
      super(message);
      this.status = status;
    }
  },
}));

import { getJob, listJobs } from "./api/client";

const SUMMARY: JobSummary = {
  id: "job-1",
  author: "Jane Recruiter",
  url: "https://linkedin.com/feed/update/1",
  status: "RELEVANT",
  published_at: null,
  scraped_at: "2026-09-01T10:05:00Z",
  job_type: "Python",
  skills: ["Python"],
  recommended_cv: null,
};

const DETAIL: JobDetailDto = {
  ...SUMMARY,
  seniority: null,
  languages: [],
  frameworks: [],
  cloud: [],
  ai_related: null,
  matching_skills: [],
  missing_skills: [],
  subject: null,
  generated_email: null,
};

describe("App", () => {
  beforeEach(() => {
    vi.mocked(listJobs).mockReset();
    vi.mocked(getJob).mockReset();
  });

  it("navigates from the job list to the job detail view and back", async () => {
    vi.mocked(listJobs).mockResolvedValue([SUMMARY]);
    vi.mocked(getJob).mockResolvedValueOnce(DETAIL);
    const user = userEvent.setup();

    render(<App />);

    await screen.findByText("Jane Recruiter");
    await user.click(screen.getByRole("button", { name: "View" }));

    expect(await screen.findByRole("button", { name: /back to list/i })).toBeInTheDocument();
    expect(getJob).toHaveBeenCalledWith("job-1");

    await user.click(screen.getByRole("button", { name: /back to list/i }));

    expect(await screen.findByRole("button", { name: "View" })).toBeInTheDocument();
  });
});
