import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { DecisionCard } from "./DecisionCard";

const TASK = "task1111-1111-1111-1111-111111111111";
const DOC = "11111111-1111-1111-1111-111111111111";

test("Submit is disabled until a valid decision (comment required to reject)", async () => {
  const u = userEvent.setup();
  const { getByRole, getByLabelText } = renderWithProviders(
    <DecisionCard taskId={TASK} documentId={DOC} />,
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
    <DecisionCard taskId={TASK} documentId={DOC} />,
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
    <DecisionCard taskId={TASK} documentId={DOC} />,
    { route: `/tasks/${TASK}` },
  );
  await u.click(getByLabelText("Approve"));
  await u.click(getByLabelText(/Signing as/));
  await u.click(getByRole("button", { name: "Submit decision" }));
  expect(await findByText(/separation of duties/i)).toBeInTheDocument();
});

test("has no a11y violations", async () => {
  const { container } = renderWithProviders(<DecisionCard taskId={TASK} documentId={DOC} />, {
    route: `/tasks/${TASK}`,
  });
  expect(await axe(container)).toHaveNoViolations();
});
