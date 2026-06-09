import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { http, HttpResponse } from "msw";
import { Route, Routes } from "react-router-dom";
import { expect, test } from "vitest";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { AuditDetailPage } from "./AuditDetailPage";

function grant(keys: string[]) {
  server.use(
    http.get("/api/v1/me/permissions", () =>
      HttpResponse.json({
        scope: { level: "SYSTEM", selector: null },
        permissions: keys.map((key) => ({ key, effect: "ALLOW", source: null })),
      }),
    ),
  );
}

function harness(id: string) {
  return renderWithProviders(
    <Routes>
      <Route path="/audits/:id" element={<AuditDetailPage />} />
    </Routes>,
    { route: `/audits/${id}` },
  );
}

test("renders header (identifier · title · state) + plan/programme context", async () => {
  harness("au000001-0001-0001-0001-000000000001");
  expect(await screen.findByText("REC-000061")).toBeInTheDocument();
  expect(screen.getByText("Purchasing & Suppliers audit")).toBeInTheDocument();
  // getAllBy: once Task 13 mounts the stepper, "● In progress" appears twice (badge + current node).
  expect(screen.getAllByText(/● In progress/).length).toBeGreaterThan(0);
  expect(screen.getByText("Mara Quality")).toBeInTheDocument(); // lead via directory
  // Plan context: scheduled date + checklist ref + auditee process + the programme title.
  expect(await screen.findByText(/2026-05-28/)).toBeInTheDocument();
  expect(screen.getByText(/FRM-AUD-002/)).toBeInTheDocument();
  // getAllBy: FindingsCard now also renders "Purchasing" as a process_ref badge (Task 14).
  expect(screen.getAllByText(/Purchasing$/).length).toBeGreaterThan(0);
  expect(screen.getByText(/2026 Internal Audit Programme/)).toBeInTheDocument();
});

test("404 → a calm not-found panel", async () => {
  harness("au-missing-0000-0000-0000-000000000000");
  expect(await screen.findByText(/Audit not found/)).toBeInTheDocument();
});

test("403 → a calm no-access panel", async () => {
  server.use(
    http.get("/api/v1/audits/:id", () =>
      HttpResponse.json({ code: "forbidden", title: "Forbidden" }, { status: 403 }),
    ),
  );
  harness("au000001-0001-0001-0001-000000000001");
  expect(await screen.findByText(/don't have access to internal audits/)).toBeInTheDocument();
});

test("no axe violations", async () => {
  const { container } = harness("au000001-0001-0001-0001-000000000001");
  await screen.findByText("REC-000061");
  expect(await axe(container)).toHaveNoViolations();
});

// diff-critic MAJOR regression: the modal must unmount on close — without it, the post-NC
// confirmation state survives a reopen and the auditor can never log a second finding.
test("reopening Log finding after an NC success shows a FRESH form, not the stale confirmation", async () => {
  grant(["finding.create"]);
  const u = userEvent.setup();
  harness("au000001-0001-0001-0001-000000000001");
  // Settle the plan context first: the write scope flips SYSTEM→PROCESS when the plan resolves,
  // remounting the perms query (and the button) — clicking before the flip hits a detached node.
  await screen.findByText(/FRM-AUD-002/);
  // First log: NC → severity → submit → the auto-CAPA confirmation appears.
  await u.click(await screen.findByRole("button", { name: /Log finding/ }));
  let dialog = await screen.findByRole("dialog");
  await u.click(within(dialog).getByLabelText(/Type/));
  await u.click(await screen.findByRole("option", { name: "NC" }));
  await u.click(within(dialog).getByLabelText(/Severity/));
  await u.click(await screen.findByRole("option", { name: "Major" }));
  await u.click(within(dialog).getByRole("button", { name: /Log finding/ }));
  expect(await within(dialog).findByText(/CAPA auto-created/)).toBeInTheDocument();
  await u.click(within(dialog).getByRole("button", { name: /Done/ }));
  // Reopen: the form must be fresh (Type select present, no stale confirmation).
  await u.click(await screen.findByRole("button", { name: /Log finding/ }));
  dialog = await screen.findByRole("dialog");
  expect(within(dialog).getByLabelText(/Type/)).toBeInTheDocument();
  expect(within(dialog).queryByText(/CAPA auto-created/)).toBeNull();
});
