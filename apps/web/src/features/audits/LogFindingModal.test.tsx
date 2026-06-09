import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";
import { axe } from "jest-axe";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { LogFindingModal } from "./LogFindingModal";

const AUDIT_ID = "au000001-0001-0001-0001-000000000001";

test("an NC requires a severity (disabled until picked); POSTs the body", async () => {
  let body: Record<string, unknown> | null = null;
  server.use(
    http.post("/api/v1/audits/:id/findings", async ({ request }) => {
      body = (await request.json()) as typeof body;
      return HttpResponse.json(
        { id: "fd-new", identifier: "REC-000070", title: String(body!["summary"]), audit_id: AUDIT_ID, finding_type: "NC", severity: "Major", clause_ref: "8.4", process_ref: null, auto_capa_id: "ca-auto-1", correction_of: null, superseded_by_correction: null },
        { status: 201 },
      );
    }),
  );
  const u = userEvent.setup();
  renderWithProviders(<LogFindingModal auditId={AUDIT_ID} opened onClose={() => {}} />);
  const dialog = await screen.findByRole("dialog");
  await u.click(within(dialog).getByLabelText(/Type/));
  await u.click(await screen.findByRole("option", { name: "NC" }));
  expect(within(dialog).getByRole("button", { name: /Log finding/ })).toBeDisabled();
  await u.click(within(dialog).getByLabelText(/Severity/));
  await u.click(await screen.findByRole("option", { name: "Major" }));
  await u.type(within(dialog).getByLabelText(/Summary/), "Re-evaluation overdue");
  await u.type(within(dialog).getByLabelText(/Clause ref/), "8.4");
  await u.click(within(dialog).getByRole("button", { name: /Log finding/ }));
  await waitFor(() => expect(body).not.toBeNull());
  expect(body!["finding_type"]).toBe("NC");
  expect(body!["severity"]).toBe("Major");
  expect(body!["summary"]).toBe("Re-evaluation overdue");
});

test("an NC success shows the auto-CAPA confirmation with the deep-link", async () => {
  const u = userEvent.setup();
  renderWithProviders(<LogFindingModal auditId={AUDIT_ID} opened onClose={() => {}} />);
  const dialog = await screen.findByRole("dialog");
  await u.click(within(dialog).getByLabelText(/Type/));
  await u.click(await screen.findByRole("option", { name: "NC" }));
  await u.click(within(dialog).getByLabelText(/Severity/));
  await u.click(await screen.findByRole("option", { name: "Major" }));
  await u.click(within(dialog).getByRole("button", { name: /Log finding/ }));
  // The default handler returns createdNcFindingFixture (auto_capa_id set).
  expect(await within(dialog).findByText(/CAPA auto-created/)).toBeInTheDocument();
  expect(within(dialog).getByRole("link", { name: /View CAPA/ })).toHaveAttribute(
    "href",
    "/capa?capa=ca-auto-00-0000-0000-0000-000000000000",
  );
});

test("an OBSERVATION needs no severity and closes on success", async () => {
  let closed = false;
  server.use(
    http.post("/api/v1/audits/:id/findings", () =>
      HttpResponse.json(
        { id: "fd-obs", identifier: "REC-000071", title: "Obs", audit_id: AUDIT_ID, finding_type: "OBSERVATION", severity: null, clause_ref: null, process_ref: null, auto_capa_id: null, correction_of: null, superseded_by_correction: null },
        { status: 201 },
      ),
    ),
  );
  const u = userEvent.setup();
  renderWithProviders(<LogFindingModal auditId={AUDIT_ID} opened onClose={() => (closed = true)} />);
  const dialog = await screen.findByRole("dialog");
  await u.click(within(dialog).getByLabelText(/Type/));
  await u.click(await screen.findByRole("option", { name: "Observation" }));
  expect(within(dialog).getByRole("button", { name: /Log finding/ })).toBeEnabled();
  await u.click(within(dialog).getByRole("button", { name: /Log finding/ }));
  await waitFor(() => expect(closed).toBe(true));
});

test("a server error (409 audit closed) renders calmly in the modal", async () => {
  server.use(
    http.post("/api/v1/audits/:id/findings", () =>
      HttpResponse.json(
        { code: "audit_finding_audit_closed", title: "Cannot add a finding to a Closed audit" },
        { status: 409 },
      ),
    ),
  );
  const u = userEvent.setup();
  renderWithProviders(<LogFindingModal auditId={AUDIT_ID} opened onClose={() => {}} />);
  const dialog = await screen.findByRole("dialog");
  await u.click(within(dialog).getByLabelText(/Type/));
  await u.click(await screen.findByRole("option", { name: "OFI" }));
  await u.click(within(dialog).getByRole("button", { name: /Log finding/ }));
  expect(await within(dialog).findByText(/Cannot add a finding to a Closed audit/)).toBeInTheDocument();
});

test("no axe violations with the modal open (the spec §9 modal-open gate)", async () => {
  renderWithProviders(<LogFindingModal auditId={AUDIT_ID} opened onClose={() => {}} />);
  const dialog = await screen.findByRole("dialog");
  expect(await axe(dialog)).toHaveNoViolations();
});
