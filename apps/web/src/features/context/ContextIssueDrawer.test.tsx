import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { axe } from "jest-axe";
import { expect, it } from "vitest";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import { ContextIssueDrawer } from "./ContextIssueDrawer";

const STRENGTH_ID = "cc000001-0001-0001-0001-000000000001"; // internal · strength · active

it("renders the classification, category, status, description, and last-reviewed date", async () => {
  const { container } = renderWithProviders(
    <ContextIssueDrawer issueId={STRENGTH_ID} onClose={() => {}} headEditable canManage={false} />,
  );
  await waitFor(() =>
    expect(screen.getByText("Skilled and certified QA team")).toBeInTheDocument(),
  );
  expect(screen.getByText("Internal")).toBeInTheDocument();
  expect(screen.getByText("Strength")).toBeInTheDocument();
  expect(screen.getByText("Active")).toBeInTheDocument();
  expect(screen.getByText("2026-06-01")).toBeInTheDocument();
  // no edit affordance without can_manage (a read it can view but not steward)
  expect(screen.queryByRole("button", { name: "Edit issue" })).not.toBeInTheDocument();
  expect(await axe(container)).toHaveNoViolations();
});

it("shows Edit (opening the edit modal) when can_manage AND the head is editable", async () => {
  const user = userEvent.setup();
  renderWithProviders(
    <ContextIssueDrawer issueId={STRENGTH_ID} onClose={() => {}} headEditable canManage />,
  );
  await user.click(await screen.findByRole("button", { name: "Edit issue" }));
  // the drawer (a dialog) + the edit modal (a dialog) coexist → assert the modal by its title text
  expect(await screen.findByText(/edit context issue/i)).toBeInTheDocument();
});

it("hides Edit and explains read-only when can_manage but the head is NOT editable", async () => {
  renderWithProviders(
    <ContextIssueDrawer issueId={STRENGTH_ID} onClose={() => {}} headEditable={false} canManage />,
  );
  await waitFor(() =>
    expect(screen.getByText("Skilled and certified QA team")).toBeInTheDocument(),
  );
  expect(screen.queryByRole("button", { name: "Edit issue" })).not.toBeInTheDocument();
  expect(screen.getByText(/isn.t in an editable state/i)).toBeInTheDocument();
});

it("shows a calm no-access panel on a 403 (never crashes)", async () => {
  server.use(
    http.get("/api/v1/context/:id", () =>
      HttpResponse.json({ code: "permission_denied", title: "no" }, { status: 403 }),
    ),
  );
  renderWithProviders(
    <ContextIssueDrawer issueId={STRENGTH_ID} onClose={() => {}} headEditable canManage />,
  );
  await waitFor(() =>
    expect(screen.getByText("You don't have access to this context issue.")).toBeInTheDocument(),
  );
});
