import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { ProgrammePage } from "./ProgrammePage";

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

test("lists programmes with the archived badge; write affordances hidden without audit.plan", async () => {
  renderWithProviders(<ProgrammePage />, { route: "/audits/programme" });
  expect(await screen.findByText("AUDPROG-000001")).toBeInTheDocument();
  const archived = screen.getByRole("row", { name: /AUDPROG-000002/ });
  expect(within(archived).getByText(/Archived/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /New programme/ })).toBeNull();
  expect(screen.queryByRole("button", { name: /Edit/ })).toBeNull();
});

test("creating a programme POSTs title + period", async () => {
  grant(["audit.plan"]);
  let body: { title?: string; period?: string } | null = null;
  server.use(
    http.post("/api/v1/audit-programs", async ({ request }) => {
      body = (await request.json()) as typeof body;
      return HttpResponse.json(
        { id: "ap-new-00-0000-0000-0000-000000000000", identifier: "AUDPROG-000003", title: body!.title!, period: body!.period ?? null, coverage: null, archived: false, created_at: "2026-06-09T09:00:00+00:00" },
        { status: 201 },
      );
    }),
  );
  const u = userEvent.setup();
  renderWithProviders(<ProgrammePage />, { route: "/audits/programme" });
  await u.click(await screen.findByRole("button", { name: /New programme/ }));
  const dialog = await screen.findByRole("dialog");
  await u.type(within(dialog).getByLabelText(/Title/), "2027 Programme");
  await u.type(within(dialog).getByLabelText(/Period/), "2027");
  await u.click(within(dialog).getByRole("button", { name: /Save programme/ }));
  await waitFor(() => expect(body).not.toBeNull());
  expect(body!.title).toBe("2027 Programme");
  expect(body!.period).toBe("2027");
});

test("editing pre-fills and PATCHes; the archive toggle rides the same form", async () => {
  grant(["audit.plan"]);
  let body: { title?: string; archived?: boolean } | null = null;
  server.use(
    http.patch("/api/v1/audit-programs/:id", async ({ request, params }) => {
      body = (await request.json()) as typeof body;
      return HttpResponse.json({ id: String(params.id), identifier: "AUDPROG-000001", title: "2026 Internal Audit Programme", period: "2026", coverage: null, archived: true, created_at: "2026-01-05T09:00:00+00:00" });
    }),
  );
  const u = userEvent.setup();
  renderWithProviders(<ProgrammePage />, { route: "/audits/programme" });
  const row = await screen.findByRole("row", { name: /AUDPROG-000001/ });
  await u.click(within(row).getByRole("button", { name: /Edit/ }));
  const dialog = await screen.findByRole("dialog");
  expect(within(dialog).getByLabelText(/Title/)).toHaveValue("2026 Internal Audit Programme");
  await u.click(within(dialog).getByLabelText(/Archived/));
  await u.click(within(dialog).getByRole("button", { name: /Save programme/ }));
  await waitFor(() => expect(body).not.toBeNull());
  expect(body!.archived).toBe(true);
});

test("renders a calm no-access panel on a 403 (audit.read)", async () => {
  server.use(
    http.get("/api/v1/audit-programs", () =>
      HttpResponse.json({ code: "forbidden", title: "Forbidden" }, { status: 403 }),
    ),
  );
  renderWithProviders(<ProgrammePage />, { route: "/audits/programme" });
  expect(await screen.findByText(/don't have access to the audit programme/)).toBeInTheDocument();
});

test("no axe violations", async () => {
  const { container } = renderWithProviders(<ProgrammePage />, { route: "/audits/programme" });
  await screen.findByText("AUDPROG-000001");
  expect(await axe(container)).toHaveNoViolations();
});

test("shows the selected programme's plans (process + lead resolved, degrade-friendly)", async () => {
  renderWithProviders(<ProgrammePage />, { route: "/audits/programme" });
  // Newest programme (AUDPROG-000001) is selected by default → its plans render.
  expect(await screen.findByText("Plans — AUDPROG-000001")).toBeInTheDocument();
  const planRows = await screen.findAllByRole("row", { name: /2026-/ });
  expect(within(planRows[0]!).getByText("2026-05-28")).toBeInTheDocument();
  expect(within(planRows[0]!).getByText("Purchasing")).toBeInTheDocument(); // process name
  expect(within(planRows[0]!).getByText("Mara Quality")).toBeInTheDocument(); // lead via directory
  expect(within(planRows[0]!).getByText("FRM-AUD-002")).toBeInTheDocument();
});

test("Add plan POSTs to the selected programme (date + process + checklist ref)", async () => {
  grant(["audit.plan"]);
  let body: Record<string, unknown> | null = null;
  let target = "";
  server.use(
    http.post("/api/v1/audit-programs/:id/plans", async ({ request, params }) => {
      target = String(params.id);
      body = (await request.json()) as typeof body;
      return HttpResponse.json(
        { id: "pl-new-00-0000-0000-0000-000000000000", program_id: target, auditee_process_id: null, lead_auditor_user_id: null, scheduled_date: "2026-11-01", checklist_ref: "FRM-AUD-002", created_at: "2026-06-09T09:00:00+00:00" },
        { status: 201 },
      );
    }),
  );
  const u = userEvent.setup();
  renderWithProviders(<ProgrammePage />, { route: "/audits/programme" });
  await u.click(await screen.findByRole("button", { name: /Add plan/ }));
  const dialog = await screen.findByRole("dialog");
  await u.type(within(dialog).getByLabelText(/Scheduled date/), "2026-11-01");
  await u.click(within(dialog).getByLabelText(/Auditee process/));
  await u.click(await screen.findByRole("option", { name: "Purchasing" }));
  await u.type(within(dialog).getByLabelText(/Checklist ref/), "FRM-AUD-002");
  await u.click(within(dialog).getByRole("button", { name: /Save plan/ }));
  await waitFor(() => expect(body).not.toBeNull());
  expect(target).toBe("ap000001-0001-0001-0001-000000000001");
  expect(body!["scheduled_date"]).toBe("2026-11-01");
  expect(body!["auditee_process_id"]).toBe("pr000001-0001-0001-0001-000000000001");
  expect(body!["checklist_ref"]).toBe("FRM-AUD-002");
});

test("an archived selected programme hides Add plan; a racing 409 surfaces calmly", async () => {
  grant(["audit.plan"]);
  const u = userEvent.setup();
  renderWithProviders(<ProgrammePage />, { route: "/audits/programme" });
  // Select the archived programme → no Add plan.
  await u.click(await screen.findByText("AUDPROG-000002"));
  expect(screen.queryByRole("button", { name: /Add plan/ })).toBeNull();
  // Back on the active one, a server 409 (race: archived elsewhere) renders calmly in the modal.
  await u.click(screen.getByText("AUDPROG-000001"));
  server.use(
    http.post("/api/v1/audit-programs/:id/plans", () =>
      HttpResponse.json(
        { code: "program_archived", title: "Cannot add a plan to an archived programme" },
        { status: 409 },
      ),
    ),
  );
  await u.click(await screen.findByRole("button", { name: /Add plan/ }));
  const dialog = await screen.findByRole("dialog");
  await u.click(within(dialog).getByRole("button", { name: /Save plan/ }));
  expect(
    await within(dialog).findByText(/Cannot add a plan to an archived programme/),
  ).toBeInTheDocument();
});

test("the process picker is omitted when GET /processes 403s (degrade)", async () => {
  grant(["audit.plan"]);
  server.use(
    http.get("/api/v1/processes", () =>
      HttpResponse.json({ code: "forbidden", title: "Forbidden" }, { status: 403 }),
    ),
  );
  const u = userEvent.setup();
  renderWithProviders(<ProgrammePage />, { route: "/audits/programme" });
  await u.click(await screen.findByRole("button", { name: /Add plan/ }));
  const dialog = await screen.findByRole("dialog");
  expect(within(dialog).getByLabelText(/Scheduled date/)).toBeInTheDocument();
  expect(within(dialog).queryByLabelText(/Auditee process/)).toBeNull();
});

// diff-critic minor: the PATCH treats an ABSENT period as keep — clearing a set period must send "".
test("clearing a pre-filled Period sends an explicit empty string on save", async () => {
  grant(["audit.plan"]);
  let body: Record<string, unknown> | null = null;
  server.use(
    http.patch("/api/v1/audit-programs/:id", async ({ request, params }) => {
      body = (await request.json()) as typeof body;
      return HttpResponse.json({ id: String(params.id), identifier: "AUDPROG-000001", title: "2026 Internal Audit Programme", period: null, coverage: null, archived: false, created_at: "2026-01-05T09:00:00+00:00" });
    }),
  );
  const u = userEvent.setup();
  renderWithProviders(<ProgrammePage />, { route: "/audits/programme" });
  const row = await screen.findByRole("row", { name: /AUDPROG-000001/ });
  await u.click(within(row).getByRole("button", { name: /Edit/ }));
  const dialog = await screen.findByRole("dialog");
  await u.clear(within(dialog).getByLabelText(/Period/));
  await u.click(within(dialog).getByRole("button", { name: /Save programme/ }));
  await waitFor(() => expect(body).not.toBeNull());
  expect(body!["period"]).toBe("");
});
