import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import type { Finding } from "../../lib/types";
import { CorrectFindingModal } from "./CorrectFindingModal";

const nc: Finding = {
  id: "fd000001-0001-0001-0001-000000000001", identifier: "REC-000062",
  title: "Supplier re-evaluation overdue for 2 vendors",
  audit_id: "au000001-0001-0001-0001-000000000001", finding_type: "NC", severity: "Major",
  clause_ref: "8.4", process_ref: "Purchasing",
  auto_capa_id: "ca000001-0001-0001-0001-000000000001",
  correction_of: null, superseded_by_correction: null,
};

test("pre-fills from the finding; retype NC→OFI POSTs the correction with a reason", async () => {
  let body: Record<string, unknown> | null = null;
  let path = "";
  server.use(
    http.post("/api/v1/findings/:id/correction", async ({ request, params }) => {
      path = String(params.id);
      body = (await request.json()) as typeof body;
      return HttpResponse.json(
        { id: "fd-corr", identifier: "REC-000072", title: "Declassified", audit_id: nc.audit_id, finding_type: "OFI", severity: null, clause_ref: "8.4", process_ref: "Purchasing", auto_capa_id: null, correction_of: nc.id, superseded_by_correction: null },
        { status: 201 },
      );
    }),
  );
  const u = userEvent.setup();
  renderWithProviders(
    <CorrectFindingModal finding={nc} auditId={nc.audit_id} opened onClose={() => {}} />,
  );
  const dialog = await screen.findByRole("dialog");
  expect(within(dialog).getByLabelText(/Clause ref/)).toHaveValue("8.4");
  await u.click(within(dialog).getByLabelText(/Type/));
  await u.click(await screen.findByRole("option", { name: "OFI" }));
  await u.type(within(dialog).getByLabelText(/Reason/), "Declassified");
  await u.click(within(dialog).getByRole("button", { name: /Save correction/ }));
  await waitFor(() => expect(body).not.toBeNull());
  expect(path).toBe(nc.id);
  expect(body!["finding_type"]).toBe("OFI");
  expect(body!["reason"]).toBe("Declassified");
});

test("retype TO NC requires a severity (disabled until picked)", async () => {
  const u = userEvent.setup();
  renderWithProviders(
    <CorrectFindingModal
      finding={{ ...nc, finding_type: "OBSERVATION", severity: null, auto_capa_id: null }}
      auditId={nc.audit_id}
      opened
      onClose={() => {}}
    />,
  );
  const dialog = await screen.findByRole("dialog");
  await u.click(within(dialog).getByLabelText(/Type/));
  await u.click(await screen.findByRole("option", { name: "NC" }));
  expect(within(dialog).getByRole("button", { name: /Save correction/ })).toBeDisabled();
  await u.click(within(dialog).getByLabelText(/Severity/));
  await u.click(await screen.findByRole("option", { name: "Minor" }));
  expect(within(dialog).getByRole("button", { name: /Save correction/ })).toBeEnabled();
});

test("409 finding_already_corrected renders calmly", async () => {
  server.use(
    http.post("/api/v1/findings/:id/correction", () =>
      HttpResponse.json(
        { code: "finding_already_corrected", title: "This finding is already superseded" },
        { status: 409 },
      ),
    ),
  );
  const u = userEvent.setup();
  renderWithProviders(
    <CorrectFindingModal finding={nc} auditId={nc.audit_id} opened onClose={() => {}} />,
  );
  const dialog = await screen.findByRole("dialog");
  await u.click(within(dialog).getByRole("button", { name: /Save correction/ }));
  expect(await within(dialog).findByText(/already superseded/)).toBeInTheDocument();
});
