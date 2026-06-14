import { http, HttpResponse } from "msw";
import { expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import type { DcrDetail } from "../../lib/types";
import { EditDcrModal } from "./EditDcrModal";
import { CancelDcrModal } from "./CancelDcrModal";

const DCR = {
  id: "dcr00001-0001-0001-0001-000000000001",
  identifier: "DCR-2026-0001",
  target_document_id: "doc00001-0001-0001-0001-000000000001",
  change_type: "REVISE",
  change_significance: "MAJOR",
  reason_class: "capa",
  reason_text: "Original reason.",
  source_link_type: "capa",
  source_link_id: "capa0001-0001-0001-0001-000000000001",
  proposed_effective_from: null,
  resulting_version_id: null,
  state: "Open",
  decision: null,
  created_by: "bbbb1111-1111-1111-1111-111111111111",
  created_at: "2026-06-10T09:00:00+00:00",
  stage_events: [],
} satisfies DcrDetail;

it("edits a DCR's reason and closes on success", async () => {
  const onClose = vi.fn();
  renderWithProviders(<EditDcrModal dcr={DCR} onClose={onClose} />);
  const reason = screen.getByLabelText(/Reason for change/);
  await userEvent.clear(reason);
  await userEvent.type(reason, "Updated reason.");
  await userEvent.click(screen.getByRole("button", { name: "Save" }));
  await vi.waitFor(() => expect(onClose).toHaveBeenCalled());
});

it("surfaces a 409 dcr_not_editable calmly", async () => {
  server.use(
    http.patch("/api/v1/dcrs/:id", () =>
      HttpResponse.json(
        { code: "dcr_not_editable", title: "Conflict", detail: "A DCR can only be edited while Open" },
        { status: 409 },
      ),
    ),
  );
  const onClose = vi.fn();
  renderWithProviders(<EditDcrModal dcr={DCR} onClose={onClose} />);
  await userEvent.click(screen.getByRole("button", { name: "Save" }));
  expect(await screen.findByText("A DCR can only be edited while Open")).toBeInTheDocument();
  expect(onClose).not.toHaveBeenCalled();
});

it("cancels a DCR with an optional comment", async () => {
  const onClose = vi.fn();
  renderWithProviders(<CancelDcrModal dcr={DCR} onClose={onClose} />);
  await userEvent.type(screen.getByLabelText("Comment (optional)"), "Withdrawn.");
  await userEvent.click(screen.getByRole("button", { name: "Cancel change request" }));
  await vi.waitFor(() => expect(onClose).toHaveBeenCalled());
});

it("surfaces a 409 dcr_not_cancellable calmly", async () => {
  server.use(
    http.post("/api/v1/dcrs/:id/cancel", () =>
      HttpResponse.json(
        { code: "dcr_not_cancellable", title: "Conflict", detail: "A DCR in Approved cannot be cancelled" },
        { status: 409 },
      ),
    ),
  );
  const onClose = vi.fn();
  renderWithProviders(<CancelDcrModal dcr={DCR} onClose={onClose} />);
  await userEvent.click(screen.getByRole("button", { name: "Cancel change request" }));
  expect(await screen.findByText("A DCR in Approved cannot be cancelled")).toBeInTheDocument();
  expect(onClose).not.toHaveBeenCalled();
});

it("does not offer the MR-reserved reason class when editing a non-MR DCR", async () => {
  renderWithProviders(<EditDcrModal dcr={DCR} onClose={vi.fn()} />);
  await userEvent.click(screen.getByLabelText(/Reason class/));
  expect(await screen.findByRole("option", { name: "CAPA" })).toBeInTheDocument();
  expect(screen.queryByRole("option", { name: "Management review" })).toBeNull();
});

it("keeps the MR reason class selectable when editing an MR-sourced DCR", async () => {
  const mrDcr = { ...DCR, reason_class: "mgmt_review", source_link_type: "mgmt_review" } satisfies DcrDetail;
  renderWithProviders(<EditDcrModal dcr={mrDcr} onClose={vi.fn()} />);
  await userEvent.click(screen.getByLabelText(/Reason class/));
  expect(await screen.findByRole("option", { name: "Management review" })).toBeInTheDocument();
});

it("refuses to silently clear a set effective date", async () => {
  const dated = { ...DCR, proposed_effective_from: "2026-07-01T00:00:00+00:00" } satisfies DcrDetail;
  const onClose = vi.fn();
  renderWithProviders(<EditDcrModal dcr={dated} onClose={onClose} />);
  await userEvent.clear(screen.getByLabelText(/Proposed effective from/));
  await userEvent.click(screen.getByRole("button", { name: "Save" }));
  expect(await screen.findByText(/Clearing a set effective date isn't supported/)).toBeInTheDocument();
  expect(onClose).not.toHaveBeenCalled();
});
