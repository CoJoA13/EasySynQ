import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { Route, Routes } from "react-router-dom";
import { expect, test } from "vitest";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { NewAuditModal } from "./NewAuditModal";

function harness() {
  return renderWithProviders(
    <Routes>
      <Route path="/audits" element={<NewAuditModal opened onClose={() => {}} />} />
      <Route path="/audits/:id" element={<div>DETAIL-PAGE</div>} />
    </Routes>,
    { route: "/audits" },
  );
}

test("cascade: picking a programme loads its plans; submit POSTs plan_id and navigates", async () => {
  let body: { plan_id?: string; title?: string } | null = null;
  server.use(
    http.post("/api/v1/audits", async ({ request }) => {
      body = (await request.json()) as typeof body;
      return HttpResponse.json(
        { id: "au-new-00-0000-0000-0000-000000000000", identifier: "REC-000069", title: null, plan_id: body!.plan_id!, lead_auditor_user_id: null, state: "Scheduled", started_at: null, completed_at: null, result_summary: null, created_at: "2026-06-09T09:00:00+00:00" },
        { status: 201 },
      );
    }),
  );
  const u = userEvent.setup();
  harness();
  const dialog = await screen.findByRole("dialog");
  // Submit is disabled until a plan is picked.
  expect(within(dialog).getByRole("button", { name: /Create audit/ })).toBeDisabled();
  await u.click(within(dialog).getByLabelText(/Programme/));
  await u.click(await screen.findByRole("option", { name: /2026 Internal Audit Programme/ }));
  await u.click(within(dialog).getByLabelText(/^Plan/));
  await u.click(await screen.findByRole("option", { name: /2026-05-28/ }));
  await u.type(within(dialog).getByLabelText(/Title/), "Purchasing audit Q3");
  await u.click(within(dialog).getByRole("button", { name: /Create audit/ }));
  await waitFor(() => expect(body).not.toBeNull());
  expect(body!.plan_id).toBe("pl000001-0001-0001-0001-000000000001");
  expect(body!.title).toBe("Purchasing audit Q3");
  expect(await screen.findByText("DETAIL-PAGE")).toBeInTheDocument();
});

test("calm empty-state guidance when no programmes exist", async () => {
  server.use(http.get("/api/v1/audit-programs", () => HttpResponse.json({ data: [] })));
  harness();
  expect(await screen.findByText(/No audit plans yet/)).toBeInTheDocument();
  expect(screen.getByText(/Programme tab/)).toBeInTheDocument();
});
