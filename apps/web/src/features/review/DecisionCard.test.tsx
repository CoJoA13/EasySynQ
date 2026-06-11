import userEvent from "@testing-library/user-event";
import { screen, waitFor } from "@testing-library/react";
import { axe } from "jest-axe";
import { http, HttpResponse } from "msw";
import { describe, expect, test } from "vitest";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { DecisionCard } from "./DecisionCard";
import type { DecisionSubjectType } from "../../lib/types";

const TASK = "task1111-1111-1111-1111-111111111111";
const DOC = "11111111-1111-1111-1111-111111111111";

function renderCard({ subjectType = "DOCUMENT" as DecisionSubjectType } = {}) {
  return renderWithProviders(
    <DecisionCard taskId={TASK} subjectType={subjectType} subjectId={DOC} />,
    { route: `/tasks/${TASK}` },
  );
}

test("Submit is disabled until a valid decision (comment required to reject)", async () => {
  const u = userEvent.setup();
  const { getByRole, getByLabelText } = renderWithProviders(
    <DecisionCard taskId={TASK} subjectType="DOCUMENT" subjectId={DOC} />,
    { route: `/tasks/${TASK}` },
  );
  const submit = getByRole("button", { name: "Submit decision" });
  expect(submit).toBeDisabled();
  await u.click(getByLabelText("Reject"));
  expect(submit).toBeDisabled(); // comment still required
  await u.type(getByLabelText(/Comment/), "missing risk section");
  expect(submit).toBeEnabled();
});

test("approve requires the signature confirmation before submit", async () => {
  const u = userEvent.setup();
  const { getByRole, getByLabelText } = renderWithProviders(
    <DecisionCard taskId={TASK} subjectType="DOCUMENT" subjectId={DOC} />,
    { route: `/tasks/${TASK}` },
  );
  const submit = getByRole("button", { name: "Submit decision" });
  await u.click(getByLabelText("Approve"));
  expect(submit).toBeDisabled(); // signature not yet confirmed
  await u.click(getByLabelText(/Signing as/));
  expect(submit).toBeEnabled();
});

test("surfaces a 403 sod_violation calmly", async () => {
  server.use(
    http.post("/api/v1/tasks/:id/decision", () =>
      HttpResponse.json({ code: "sod_violation", title: "Forbidden" }, { status: 403 }),
    ),
  );
  const u = userEvent.setup();
  const { getByRole, getByLabelText, findByText } = renderWithProviders(
    <DecisionCard taskId={TASK} subjectType="DOCUMENT" subjectId={DOC} />,
    { route: `/tasks/${TASK}` },
  );
  await u.click(getByLabelText("Approve"));
  await u.click(getByLabelText(/Signing as/));
  await u.click(getByRole("button", { name: "Submit decision" }));
  expect(await findByText(/separation of duties/i)).toBeInTheDocument();
});

test("has no a11y violations", async () => {
  const { container } = renderWithProviders(<DecisionCard taskId={TASK} subjectType="DOCUMENT" subjectId={DOC} />, {
    route: `/tasks/${TASK}`,
  });
  expect(await axe(container)).toHaveNoViolations();
});

describe("DecisionCard — PERIODIC_REVIEW", () => {
  test("offers complete + changes_requested only", () => {
    renderCard({ subjectType: "PERIODIC_REVIEW" });
    expect(screen.getByLabelText("Confirm — no change needed")).toBeInTheDocument();
    expect(screen.getByLabelText("Changes needed — a revision is required")).toBeInTheDocument();
    expect(screen.queryByLabelText("Approve")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Reject")).not.toBeInTheDocument();
  });

  test("complete requires the review-confirmed signature and posts outcome=complete", async () => {
    const bodies: unknown[] = [];
    server.use(
      http.post("/api/v1/tasks/:id/decision", async ({ request }) => {
        bodies.push(await request.json());
        return HttpResponse.json({
          current_state: "COMPLETED",
          replayed: false,
          document_id: "11111111-1111-1111-1111-111111111111",
          next_review_due: "2028-06-10",
          signature_event_id: "se111111-1111-1111-1111-111111111111",
        });
      }),
    );
    renderCard({ subjectType: "PERIODIC_REVIEW" });
    await userEvent.click(screen.getByLabelText("Confirm — no change needed"));
    expect(screen.getByRole("button", { name: "Submit decision" })).toBeDisabled();
    expect(screen.getByText(/meaning: review confirmed/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("checkbox"));
    await userEvent.click(screen.getByRole("button", { name: "Submit decision" }));
    await waitFor(() => expect(bodies).toEqual([{ outcome: "complete" }]));
  });

  test("changes_requested requires a comment", async () => {
    renderCard({ subjectType: "PERIODIC_REVIEW" });
    await userEvent.click(screen.getByLabelText("Changes needed — a revision is required"));
    expect(screen.getByRole("button", { name: "Submit decision" })).toBeDisabled();
    await userEvent.type(screen.getByLabelText(/Comment/), "Out of date — supplier tiers changed");
    expect(screen.getByRole("button", { name: "Submit decision" })).toBeEnabled();
  });

  test("a 409 renders the no-Effective-version copy, not 'already decided'", async () => {
    server.use(
      http.post("/api/v1/tasks/:id/decision", () =>
        HttpResponse.json(
          { code: "conflict", title: "Document no longer has an Effective version to confirm" },
          { status: 409 },
        ),
      ),
    );
    renderCard({ subjectType: "PERIODIC_REVIEW" });
    await userEvent.click(screen.getByLabelText("Confirm — no change needed"));
    await userEvent.click(screen.getByRole("checkbox"));
    await userEvent.click(screen.getByRole("button", { name: "Submit decision" }));
    expect(
      await screen.findByText(/no longer has an Effective version to confirm/),
    ).toBeInTheDocument();
    expect(screen.queryByText("This task was already decided.")).not.toBeInTheDocument();
  });

  test("a stale double-decide 409 keeps the already-decided copy (diff-critic)", async () => {
    // The engine's "Task already decided" 409 (a second tab with its own idempotency key) must
    // NOT be misreported as the document having been obsoleted.
    server.use(
      http.post("/api/v1/tasks/:id/decision", () =>
        HttpResponse.json({ code: "conflict", title: "Task already decided" }, { status: 409 }),
      ),
    );
    renderCard({ subjectType: "PERIODIC_REVIEW" });
    await userEvent.click(screen.getByLabelText("Confirm — no change needed"));
    await userEvent.click(screen.getByRole("checkbox"));
    await userEvent.click(screen.getByRole("button", { name: "Submit decision" }));
    expect(await screen.findByText("This task was already decided.")).toBeInTheDocument();
    expect(
      screen.queryByText(/no longer has an Effective version to confirm/),
    ).not.toBeInTheDocument();
  });

  test("DOCUMENT card is unchanged (regression pin)", () => {
    renderCard({ subjectType: "DOCUMENT" });
    expect(screen.getByLabelText("Approve")).toBeInTheDocument();
    expect(screen.getByLabelText("Request changes")).toBeInTheDocument();
    expect(screen.getByLabelText("Reject")).toBeInTheDocument();
  });
});
