import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/client";
import type { JobDetail } from "../api/types";
import { EmailEditor } from "./EmailEditor";

vi.mock("../api/client", () => ({
  editGeneratedEmail: vi.fn(),
  ApiError: class ApiError extends Error {
    status: number | null;
    constructor(message: string, status: number | null) {
      super(message);
      this.status = status;
    }
  },
}));

import { editGeneratedEmail } from "../api/client";

const JOB: JobDetail = {
  id: "job-1",
  author: "Jane Recruiter",
  url: "https://linkedin.com/feed/update/1",
  status: "EMAIL_GENERATED",
  published_at: null,
  scraped_at: "2026-09-01T10:05:00Z",
  job_type: "Python",
  skills: ["Python"],
  recommended_cv: "python",
  seniority: "senior",
  languages: [],
  frameworks: [],
  cloud: [],
  ai_related: false,
  matching_skills: [],
  missing_skills: [],
  subject: "Original subject",
  generated_email: "Original body",
};

/**
 * Envuelve `EmailEditor` con el mismo patrón que `JobDetail` usa en
 * producción: `onSaved` actualiza el `job` que se le vuelve a pasar como
 * prop. Sin este wrapper, la aserción de "Changes saved." nunca vería
 * `dirty === false`, porque un `job` prop fijo no refleja lo que el padre
 * real haría tras un guardado exitoso.
 */
function ControlledEmailEditor({
  initialJob,
  onSaved,
}: {
  initialJob: JobDetail;
  onSaved: (job: JobDetail) => void;
}) {
  const [job, setJob] = useState(initialJob);
  return (
    <EmailEditor
      job={job}
      onSaved={(updated) => {
        setJob(updated);
        onSaved(updated);
      }}
    />
  );
}

describe("EmailEditor", () => {
  beforeEach(() => {
    vi.mocked(editGeneratedEmail).mockReset();
  });

  it("saves edited subject/body via PATCH /api/jobs/{id}/email, never only client-side", async () => {
    const updated: JobDetail = { ...JOB, subject: "Edited subject", generated_email: "Edited body" };
    vi.mocked(editGeneratedEmail).mockResolvedValueOnce(updated);
    const onSaved = vi.fn();
    const user = userEvent.setup();

    render(<ControlledEmailEditor initialJob={JOB} onSaved={onSaved} />);

    const subjectInput = screen.getByLabelText("Subject");
    const bodyInput = screen.getByLabelText("Body");
    await user.clear(subjectInput);
    await user.type(subjectInput, "Edited subject");
    await user.clear(bodyInput);
    await user.type(bodyInput, "Edited body");
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    expect(editGeneratedEmail).toHaveBeenCalledWith("job-1", "Edited subject", "Edited body");
    expect(await screen.findByText("Changes saved.")).toBeInTheDocument();
    expect(onSaved).toHaveBeenCalledWith(updated);
  });

  it("disables Save changes until the draft differs from the persisted email", () => {
    render(<EmailEditor job={JOB} onSaved={vi.fn()} />);

    expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();
  });

  it("shows the API error (e.g. 409 because the job left EMAIL_GENERATED) instead of silently failing", async () => {
    vi.mocked(editGeneratedEmail).mockRejectedValueOnce(
      new ApiError(
        "The generated email can only be edited while the job is in EMAIL_GENERATED (current status: DRAFT_CREATED).",
        409,
      ),
    );
    const user = userEvent.setup();

    render(<EmailEditor job={JOB} onSaved={vi.fn()} />);

    await user.type(screen.getByLabelText("Subject"), " edited");
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "current status: DRAFT_CREATED",
    );
  });
});
