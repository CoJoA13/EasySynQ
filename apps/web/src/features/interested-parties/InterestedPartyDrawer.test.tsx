import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { axe } from "jest-axe";
import { expect, it } from "vitest";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import { InterestedPartyDrawer } from "./InterestedPartyDrawer";

const ACME_ID = "ee000001-0001-0001-0001-000000000001"; // customer · high · active

it("renders the type, influence, status, name, needs/expectations, and last-reviewed date", async () => {
  const { container } = renderWithProviders(
    <InterestedPartyDrawer partyId={ACME_ID} onClose={() => {}} headEditable canManage={false} />,
  );
  await waitFor(() => expect(screen.getByText("Acme Manufacturing")).toBeInTheDocument());
  expect(screen.getByText("Customer")).toBeInTheDocument();
  expect(screen.getByText("High influence")).toBeInTheDocument();
  expect(screen.getByText("Active")).toBeInTheDocument();
  expect(screen.getByText("Defect-free parts delivered to schedule.")).toBeInTheDocument();
  expect(screen.getByText("2026-06-01")).toBeInTheDocument();
  // no edit affordance without can_manage (a read it can view but not steward)
  expect(screen.queryByRole("button", { name: "Edit party" })).not.toBeInTheDocument();
  expect(await axe(container)).toHaveNoViolations();
});

it("shows Edit (opening the edit modal) when can_manage AND the head is editable", async () => {
  const user = userEvent.setup();
  renderWithProviders(
    <InterestedPartyDrawer partyId={ACME_ID} onClose={() => {}} headEditable canManage />,
  );
  await user.click(await screen.findByRole("button", { name: "Edit party" }));
  // the drawer (a dialog) + the edit modal (a dialog) coexist → assert the modal by its title text
  expect(await screen.findByText(/edit interested party/i)).toBeInTheDocument();
});

it("hides Edit and explains read-only when can_manage but the head is NOT editable", async () => {
  renderWithProviders(
    <InterestedPartyDrawer partyId={ACME_ID} onClose={() => {}} headEditable={false} canManage />,
  );
  await waitFor(() => expect(screen.getByText("Acme Manufacturing")).toBeInTheDocument());
  expect(screen.queryByRole("button", { name: "Edit party" })).not.toBeInTheDocument();
  expect(screen.getByText(/isn.t in an editable state/i)).toBeInTheDocument();
});

it("shows 'Influence unspecified' when the party has no influence rating", async () => {
  server.use(
    http.get("/api/v1/interested-parties/:id", () =>
      HttpResponse.json({
        id: ACME_ID,
        register_doc_id: "ee000000-0000-0000-0000-0000000000bb",
        party_type: "partner",
        party_name: "Regional logistics partner",
        needs_expectations: "Accurate forecasts and stable volumes.",
        influence: null,
        status: "active",
        last_reviewed_at: null,
        row_version: 1,
        created_at: null,
        updated_at: null,
      }),
    ),
  );
  renderWithProviders(
    <InterestedPartyDrawer partyId={ACME_ID} onClose={() => {}} headEditable canManage={false} />,
  );
  await waitFor(() => expect(screen.getByText("Regional logistics partner")).toBeInTheDocument());
  expect(screen.getByText("Influence unspecified")).toBeInTheDocument();
  expect(screen.getByText("Never reviewed.")).toBeInTheDocument();
});

it("shows a calm no-access panel on a 403 (never crashes)", async () => {
  server.use(
    http.get("/api/v1/interested-parties/:id", () =>
      HttpResponse.json({ code: "permission_denied", title: "no" }, { status: 403 }),
    ),
  );
  renderWithProviders(
    <InterestedPartyDrawer partyId={ACME_ID} onClose={() => {}} headEditable canManage />,
  );
  await waitFor(() =>
    expect(screen.getByText("You don't have access to this interested party.")).toBeInTheDocument(),
  );
});
