import { http, HttpResponse } from "msw";
import { expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import type { DcrDetail } from "../../lib/types";
import { DcrAdvancePanel } from "./DcrAdvancePanel";

function grant(...keys: string[]) {
  server.use(
    http.get("/api/v1/me/permissions", () =>
      HttpResponse.json({
        scope: { level: "SYSTEM", selector: null },
        permissions: keys.map((key) => ({ key, effect: "ALLOW" })),
      }),
    ),
  );
}

const base = {
  id: "dcr00001-0001-0001-0001-000000000001",
  identifier: "DCR-2026-0001",
  target_document_id: "doc00001-0001-0001-0001-000000000001",
  change_type: "REVISE",
  change_significance: "MAJOR",
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
} satisfies DcrDetail;

it("shows Edit + Cancel for an Open DCR with both keys", async () => {
  grant("changeRequest.assess", "changeRequest.close");
  renderWithProviders(<DcrAdvancePanel dcr={base} />);
  expect(await screen.findByRole("button", { name: "Edit details" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Cancel" })).toBeInTheDocument();
});

it("hides Edit once past Open but keeps Cancel through Routed", async () => {
  grant("changeRequest.assess", "changeRequest.close");
  renderWithProviders(<DcrAdvancePanel dcr={{ ...base, state: "Routed" }} />);
  expect(await screen.findByRole("button", { name: "Cancel" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Edit details" })).toBeNull();
});

it("renders no affordances in a terminal state even with both keys", async () => {
  grant("changeRequest.assess", "changeRequest.close");
  renderWithProviders(
    <>
      <div data-testid="probe" />
      <DcrAdvancePanel dcr={{ ...base, state: "Closed" }} />
    </>,
  );
  await screen.findByTestId("probe");
  await waitFor(() => expect(screen.queryByRole("button", { name: "Edit details" })).toBeNull());
  expect(screen.queryByRole("button", { name: "Cancel" })).toBeNull();
});

it("shows nothing without the keys (Open, no permissions)", async () => {
  renderWithProviders(
    <>
      <div data-testid="probe" />
      <DcrAdvancePanel dcr={base} />
    </>,
  );
  await screen.findByTestId("probe");
  await waitFor(() => expect(screen.queryByRole("button", { name: "Edit details" })).toBeNull());
  expect(screen.queryByRole("button", { name: "Cancel" })).toBeNull();
});
