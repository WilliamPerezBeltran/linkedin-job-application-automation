import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/client";
import type { ApplicationResponse, JobDetail as JobDetailDto } from "../api/types";
import { JobDetail } from "./JobDetail";

vi.mock("../api/client", () => ({
  getJob: vi.fn(),
  ignoreJob: vi.fn(),
  analyzeJob: vi.fn(),
  generateEmail: vi.fn(),
  editGeneratedEmail: vi.fn(),
  createDraft: vi.fn(),
  markApplicationSent: vi.fn(),
  ApiError: class ApiError extends Error {
    status: number | null;
    constructor(message: string, status: number | null) {
      super(message);
      this.status = status;
    }
  },
}));

import { createDraft, getJob, ignoreJob, markApplicationSent } from "../api/client";

const JOB: JobDetailDto = {
  id: "job-1",
  author: "Jane Recruiter",
  url: "https://linkedin.com/feed/update/1",
  status: "EMAIL_GENERATED",
  published_at: "2026-09-01T10:00:00Z",
  scraped_at: "2026-09-01T10:05:00Z",
  job_type: "Python",
  skills: ["Python", "FastAPI", "Docker"],
  recommended_cv: "python",
  seniority: "senior",
  languages: ["Python"],
  frameworks: ["FastAPI"],
  cloud: [],
  ai_related: false,
  matching_skills: ["Python", "FastAPI"],
  missing_skills: ["Docker"],
  subject: "Application for Python role",
  generated_email: "Hello, I would like to apply.",
};

const APPLICATION: ApplicationResponse = {
  id: "app-1",
  job_id: "job-1",
  email: "recruiter@example.com",
  subject: "Application for Python role",
  body: "Hello, I would like to apply.",
  cv_path: "cvs/python/william-python.pdf",
  gmail_draft_id: "draft-123",
  gmail_url: "https://mail.google.com/mail/u/0/#drafts/draft-123",
  status: "DRAFT_CREATED",
  sent_at: null,
};

describe("JobDetail", () => {
  beforeEach(() => {
    vi.mocked(getJob).mockReset();
    vi.mocked(ignoreJob).mockReset();
    vi.mocked(createDraft).mockReset();
    vi.mocked(markApplicationSent).mockReset();
  });

  it("renders job details, the CV match explanation and the email preview", async () => {
    vi.mocked(getJob).mockResolvedValueOnce(JOB);

    render(<JobDetail jobId="job-1" onBack={vi.fn()} />);

    expect(await screen.findByText("Jane Recruiter")).toBeInTheDocument();
    expect(screen.getByText("python")).toBeInTheDocument();
    // matching vs missing skills shown as separate lists, not a blob.
    expect(screen.getByRole("heading", { name: "Matching skills" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Missing skills" })).toBeInTheDocument();
    expect(screen.getByDisplayValue("Application for Python role")).toBeInTheDocument();
    expect(screen.getByDisplayValue("Hello, I would like to apply.")).toBeInTheDocument();
  });

  it("enables the Create Draft button only when the job is EMAIL_GENERATED", async () => {
    vi.mocked(getJob).mockResolvedValueOnce(JOB);

    render(<JobDetail jobId="job-1" onBack={vi.fn()} />);

    await screen.findByText("Jane Recruiter");
    expect(screen.getByRole("button", { name: "Create Draft" })).toBeEnabled();
  });

  it("disables the Create Draft button for a job that isn't EMAIL_GENERATED yet", async () => {
    vi.mocked(getJob).mockResolvedValueOnce({ ...JOB, status: "CV_SELECTED" });

    render(<JobDetail jobId="job-1" onBack={vi.fn()} />);

    await screen.findByText("Jane Recruiter");
    expect(screen.getByRole("button", { name: "Create Draft" })).toBeDisabled();
  });

  it("creates the Gmail draft and shows the Open in Gmail link", async () => {
    vi.mocked(getJob).mockResolvedValueOnce(JOB);
    vi.mocked(createDraft).mockResolvedValueOnce(APPLICATION);
    const user = userEvent.setup();

    render(<JobDetail jobId="job-1" onBack={vi.fn()} />);
    await screen.findByText("Jane Recruiter");
    await user.click(screen.getByRole("button", { name: "Create Draft" }));

    const link = await screen.findByRole("link", { name: "Open in Gmail" });
    expect(link).toHaveAttribute("href", APPLICATION.gmail_url);
    expect(createDraft).toHaveBeenCalledWith("job-1");
    expect(await screen.findByText("DRAFT_CREATED")).toBeInTheDocument();
    // El botón vuelve a estar deshabilitado porque el status ya no es
    // EMAIL_GENERATED (refleja el status devuelto, no reintenta la acción).
    expect(screen.getByRole("button", { name: "Create Draft" })).toBeDisabled();
  });

  it("does not show Mark as Sent before a draft has been created in this session", async () => {
    vi.mocked(getJob).mockResolvedValueOnce(JOB);

    render(<JobDetail jobId="job-1" onBack={vi.fn()} />);

    await screen.findByText("Jane Recruiter");
    expect(screen.queryByRole("button", { name: "Mark as Sent" })).not.toBeInTheDocument();
  });

  it("shows Mark as Sent once the draft is created, and marks it sent on click", async () => {
    vi.mocked(getJob).mockResolvedValueOnce(JOB);
    vi.mocked(createDraft).mockResolvedValueOnce(APPLICATION);
    vi.mocked(markApplicationSent).mockResolvedValueOnce({
      ...APPLICATION,
      status: "SENT",
      sent_at: "2026-09-17T12:00:00Z",
    });
    const user = userEvent.setup();

    render(<JobDetail jobId="job-1" onBack={vi.fn()} />);
    await screen.findByText("Jane Recruiter");
    await user.click(screen.getByRole("button", { name: "Create Draft" }));
    await screen.findByRole("link", { name: "Open in Gmail" });

    const markSentButton = screen.getByRole("button", { name: "Mark as Sent" });
    await user.click(markSentButton);

    expect(markApplicationSent).toHaveBeenCalledWith(APPLICATION.id);
    expect(await screen.findByText("SENT")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Mark as Sent" })).not.toBeInTheDocument();
  });

  it("shows the API error via ErrorBanner when mark-sent fails", async () => {
    vi.mocked(getJob).mockResolvedValueOnce(JOB);
    vi.mocked(createDraft).mockResolvedValueOnce(APPLICATION);
    vi.mocked(markApplicationSent).mockRejectedValueOnce(
      new ApiError("Application not found.", 404),
    );
    const user = userEvent.setup();

    render(<JobDetail jobId="job-1" onBack={vi.fn()} />);
    await screen.findByText("Jane Recruiter");
    await user.click(screen.getByRole("button", { name: "Create Draft" }));
    await user.click(await screen.findByRole("button", { name: "Mark as Sent" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Application not found.");
    // El status no cambió -- el botón sigue disponible para reintentar.
    expect(screen.getByRole("button", { name: "Mark as Sent" })).toBeInTheDocument();
  });

  it("does not show Mark as Sent for a job already DRAFT_CREATED without an Application loaded in this session", async () => {
    vi.mocked(getJob).mockResolvedValueOnce({ ...JOB, status: "DRAFT_CREATED" });

    render(<JobDetail jobId="job-1" onBack={vi.fn()} />);

    await screen.findByText("Jane Recruiter");
    expect(screen.queryByRole("button", { name: "Mark as Sent" })).not.toBeInTheDocument();
    expect(
      screen.getByText(/Mark as Sent unavailable in this view/),
    ).toBeInTheDocument();
  });

  it("shows the API error via ErrorBanner when create-draft fails (e.g. 409/502)", async () => {
    vi.mocked(getJob).mockResolvedValueOnce(JOB);
    vi.mocked(createDraft).mockRejectedValueOnce(
      new ApiError("The Gmail draft creation call failed. Check server logs for details.", 502),
    );
    const user = userEvent.setup();

    render(<JobDetail jobId="job-1" onBack={vi.fn()} />);
    await screen.findByText("Jane Recruiter");
    await user.click(screen.getByRole("button", { name: "Create Draft" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The Gmail draft creation call failed. Check server logs for details.",
    );
    expect(screen.queryByRole("link", { name: "Open in Gmail" })).not.toBeInTheDocument();
  });

  it("shows the API error detail when the job fails to load", async () => {
    vi.mocked(getJob).mockRejectedValueOnce(new ApiError("Job not found.", 404));

    render(<JobDetail jobId="missing" onBack={vi.fn()} />);

    expect(await screen.findByRole("alert")).toHaveTextContent("Job not found.");
  });

  it("calls onBack when the back button is clicked", async () => {
    vi.mocked(getJob).mockResolvedValueOnce(JOB);
    const onBack = vi.fn();
    const user = userEvent.setup();

    render(<JobDetail jobId="job-1" onBack={onBack} />);
    await screen.findByText("Jane Recruiter");
    await user.click(screen.getByRole("button", { name: /back to list/i }));

    expect(onBack).toHaveBeenCalled();
  });

  it("ignores the job via the API, merging the summary into the current detail", async () => {
    vi.mocked(getJob).mockResolvedValueOnce(JOB);
    vi.mocked(ignoreJob).mockResolvedValueOnce({ ...JOB, status: "IGNORED" });
    const user = userEvent.setup();

    render(<JobDetail jobId="job-1" onBack={vi.fn()} />);
    await screen.findByText("Jane Recruiter");
    await user.click(screen.getByRole("button", { name: "Ignore" }));

    expect(await screen.findByText("IGNORED")).toBeInTheDocument();
    expect(ignoreJob).toHaveBeenCalledWith("job-1");
  });
});
