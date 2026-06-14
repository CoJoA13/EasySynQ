import { expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import type { DcrCapabilities, DcrDetail, DocumentsPage } from "../../lib/types";

import { DcrAdvancePanel } from "./DcrAdvancePanel";

const ALL_CAPS: DcrCapabilities = { assess: true, route: true, implement: true, close: true };

const EMPTY_DOCS_PAGE: DocumentsPage = {
  data: [],
  page: { limit: 200, offset: 0, returned: 0, has_more: false },
};

function dcr(over: Partial<DcrDetail> = {}): DcrDetail {
  return {
    id: "dcr00001-0001-0001-0001-000000000001",
    identifier: "DCR-2026-0001",
    target_document_id: "doc00001-0001-0001-0001-000000000001",
    change_type: "REVISE",
    change_significance: "MINOR",
    reason_class: "capa",
    reason_text: "r",
    source_link_type: null,
    source_link_id: null,
    proposed_effective_from: null,
    resulting_version_id: null,
    state: "Open",
    decision: null,
    created_by: "bbbb1111-1111-1111-1111-111111111111",
    created_at: "2026-06-10T09:00:00+00:00",
    stage_events: [],
    capabilities: ALL_CAPS,
    ...over,
  } satisfies DcrDetail;
}

it("Open: shows Assess + Edit + Cancel (capabilities-gated)", () => {
  renderWithProviders(<DcrAdvancePanel dcr={dcr({ state: "Open" })} />);
  expect(screen.getByRole("button", { name: "Assess" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Edit details" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Cancel" })).toBeInTheDocument();
});

it("Assessed: shows Route + Cancel, no Assess/Edit", () => {
  renderWithProviders(<DcrAdvancePanel dcr={dcr({ state: "Assessed" })} />);
  expect(screen.getByRole("button", { name: "Route" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Cancel" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Assess" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Edit details" })).toBeNull();
});

it("InApproval: shows the awaiting banner, no advance button", () => {
  renderWithProviders(<DcrAdvancePanel dcr={dcr({ state: "InApproval" })} />);
  expect(screen.getByText(/Awaiting approval/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Route" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Cancel" })).toBeNull();
});

it("Approved REVISE: shows Implement", () => {
  renderWithProviders(<DcrAdvancePanel dcr={dcr({ state: "Approved", change_type: "REVISE" })} />);
  expect(screen.getByRole("button", { name: "Implement change" })).toBeInTheDocument();
});

it("Approved CREATE: shows Implement change + opens the new-document picker (no workspace dead-end)", async () => {
  server.use(http.get("/api/v1/documents", () => HttpResponse.json(EMPTY_DOCS_PAGE)));
  renderWithProviders(
    <DcrAdvancePanel
      dcr={dcr({ state: "Approved", change_type: "CREATE", target_document_id: null })}
    />,
  );
  // The old dead-end note is gone.
  expect(screen.queryByText(/document workspace/)).toBeNull();
  // The affordance now opens the CREATE picker modal.
  await userEvent.click(await screen.findByRole("button", { name: "Implement change" }));
  expect(await screen.findByText("Implement new-document change request")).toBeInTheDocument();
});

it("Implemented: shows Close change request", () => {
  renderWithProviders(<DcrAdvancePanel dcr={dcr({ state: "Implemented" })} />);
  expect(screen.getByRole("button", { name: "Close change request" })).toBeInTheDocument();
});

it("Closed (terminal): no affordances", async () => {
  renderWithProviders(
    <>
      <div data-testid="probe" />
      <DcrAdvancePanel dcr={dcr({ state: "Closed" })} />
    </>,
  );
  await screen.findByTestId("probe");
  await waitFor(() =>
    expect(screen.queryByRole("button", { name: "Close change request" })).toBeNull(),
  );
  expect(screen.queryByRole("button", { name: "Implement change" })).toBeNull();
});

it("hides affordances the caller's capabilities deny (Approved, implement:false)", () => {
  renderWithProviders(
    <DcrAdvancePanel
      dcr={dcr({ state: "Approved", capabilities: { ...ALL_CAPS, implement: false } })}
    />,
  );
  expect(screen.queryByRole("button", { name: "Implement change" })).toBeNull();
});
