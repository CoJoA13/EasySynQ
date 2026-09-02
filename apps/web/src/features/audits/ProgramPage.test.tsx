import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";
import { TONE_GLYPH } from "../../lib/status";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { ProgramPage } from "./ProgramPage";
import { expectSoundHeadingOutline } from "../../test/headingOutline";

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

test("lists programs with canonical status badges; write affordances hidden without audit.plan", async () => {
  renderWithProviders(<ProgramPage />, { route: "/audits/program" });
  expect(await screen.findByText("AUDPROG-000001")).toBeInTheDocument();
  const active = screen.getByRole("row", { name: /AUDPROG-000001/ });
  expect(within(active).getByLabelText("Program status: Active")).toHaveTextContent(
    TONE_GLYPH.success,
  );
  const archived = screen.getByRole("row", { name: /AUDPROG-000002/ });
  expect(within(archived).getByLabelText("Program status: Archived")).toHaveTextContent(
    TONE_GLYPH.neutral,
  );
  expect(screen.queryByRole("button", { name: /New program/ })).toBeNull();
  expect(screen.queryByRole("button", { name: /Edit/ })).toBeNull();
});

test("program rows are structural and expose one pressed native selection control", async () => {
  renderWithProviders(<ProgramPage />, { route: "/audits/program" });
  const firstRow = await screen.findByRole("row", { name: /AUDPROG-000001/ });
  const secondRow = screen.getByRole("row", { name: /AUDPROG-000002/ });
  expect(firstRow).not.toHaveAttribute("tabindex");
  expect(
    within(firstRow).getByRole("button", {
      name: "Select program AUDPROG-000001: 2026 Internal Audit Program",
      pressed: true,
    }),
  ).toBeInTheDocument();
  expect(
    within(secondRow).getByRole("button", {
      name: "Select program AUDPROG-000002: 2025 Program",
      pressed: false,
    }),
  ).toBeInTheDocument();
});

test("program arrow navigation changes focus without changing selection", async () => {
  const u = userEvent.setup();
  renderWithProviders(<ProgramPage />, { route: "/audits/program" });
  const first = await screen.findByRole("button", {
    name: /^Select program AUDPROG-000001:/,
  });
  const second = screen.getByRole("button", {
    name: /^Select program AUDPROG-000002:/,
  });
  first.focus();
  await u.keyboard("{ArrowDown}");
  expect(second).toHaveFocus();
  expect(first).toHaveAttribute("aria-pressed", "true");
  expect(second).toHaveAttribute("aria-pressed", "false");
  expect(screen.getByText("Plans — AUDPROG-000001")).toBeInTheDocument();
});

test.each(["{Enter}", " "])("the native program control selects with %s", async (key) => {
  const u = userEvent.setup();
  renderWithProviders(<ProgramPage />, { route: "/audits/program" });
  const second = await screen.findByRole("button", {
    name: /^Select program AUDPROG-000002:/,
  });
  second.focus();
  await u.keyboard(key);
  expect(second).toHaveAttribute("aria-pressed", "true");
  expect(screen.getByText("Plans — AUDPROG-000002")).toBeInTheDocument();
});

test("ordinary cells and Edit do not change program selection", async () => {
  grant(["audit.plan"]);
  const u = userEvent.setup();
  renderWithProviders(<ProgramPage />, { route: "/audits/program" });
  const secondRow = await screen.findByRole("row", { name: /AUDPROG-000002/ });
  await u.click(within(secondRow).getByText("2025"));
  expect(screen.getByText("Plans — AUDPROG-000001")).toBeInTheDocument();

  const edit = within(secondRow).getByRole("button", { name: "Edit" });
  edit.focus();
  await u.keyboard("{ArrowUp}");
  expect(edit).toHaveFocus();
  await u.click(edit);
  expect(await screen.findByRole("dialog")).toBeInTheDocument();
  expect(screen.getByText("Plans — AUDPROG-000001")).toBeInTheDocument();
});

test("creating a program POSTs title + period", async () => {
  grant(["audit.plan"]);
  let body: { title?: string; period?: string } | null = null;
  server.use(
    http.post("/api/v1/audit-programs", async ({ request }) => {
      body = (await request.json()) as typeof body;
      return HttpResponse.json(
        {
          id: "ap-new-00-0000-0000-0000-000000000000",
          identifier: "AUDPROG-000003",
          title: body!.title!,
          period: body!.period ?? null,
          coverage: null,
          archived: false,
          created_at: "2026-06-09T09:00:00+00:00",
        },
        { status: 201 },
      );
    }),
  );
  const u = userEvent.setup();
  renderWithProviders(<ProgramPage />, { route: "/audits/program" });
  await u.click(await screen.findByRole("button", { name: /New program/ }));
  const dialog = await screen.findByRole("dialog");
  await u.type(within(dialog).getByLabelText(/Title/), "2027 Program");
  await u.type(within(dialog).getByLabelText(/Period/), "2027");
  await u.click(within(dialog).getByRole("button", { name: /Save program/ }));
  await waitFor(() => expect(body).not.toBeNull());
  expect(body!.title).toBe("2027 Program");
  expect(body!.period).toBe("2027");
});

test("editing pre-fills and PATCHes; the archive toggle rides the same form", async () => {
  grant(["audit.plan"]);
  let body: { title?: string; archived?: boolean } | null = null;
  server.use(
    http.patch("/api/v1/audit-programs/:id", async ({ request, params }) => {
      body = (await request.json()) as typeof body;
      return HttpResponse.json({
        id: String(params.id),
        identifier: "AUDPROG-000001",
        title: "2026 Internal Audit Program",
        period: "2026",
        coverage: null,
        archived: true,
        created_at: "2026-01-05T09:00:00+00:00",
      });
    }),
  );
  const u = userEvent.setup();
  renderWithProviders(<ProgramPage />, { route: "/audits/program" });
  const row = await screen.findByRole("row", { name: /AUDPROG-000001/ });
  await u.click(within(row).getByRole("button", { name: /Edit/ }));
  const dialog = await screen.findByRole("dialog");
  expect(within(dialog).getByLabelText(/Title/)).toHaveValue("2026 Internal Audit Program");
  await u.click(within(dialog).getByLabelText(/Archived/));
  await u.click(within(dialog).getByRole("button", { name: /Save program/ }));
  await waitFor(() => expect(body).not.toBeNull());
  expect(body!.archived).toBe(true);
});

test("renders a calm no-access panel on a 403 (audit.read)", async () => {
  server.use(
    http.get("/api/v1/audit-programs", () =>
      HttpResponse.json({ code: "permission_denied", title: "Forbidden" }, { status: 403 }),
    ),
  );
  renderWithProviders(<ProgramPage />, { route: "/audits/program" });
  expect(await screen.findByText(/don't have access to the audit program/)).toBeInTheDocument();
});

test("no axe violations", async () => {
  const { container } = renderWithProviders(<ProgramPage />, { route: "/audits/program" });
  await screen.findByText("AUDPROG-000001");
  expect(await axe(container)).toHaveNoViolations();
  expectSoundHeadingOutline();
});

test("shows the selected program's plans (process + lead resolved, degrade-friendly)", async () => {
  renderWithProviders(<ProgramPage />, { route: "/audits/program" });
  // Newest program (AUDPROG-000001) is selected by default → its plans render.
  expect(await screen.findByText("Plans — AUDPROG-000001")).toBeInTheDocument();
  const planRows = await screen.findAllByRole("row", { name: /2026-/ });
  expect(within(planRows[0]!).getByText("2026-05-28")).toBeInTheDocument();
  expect(within(planRows[0]!).getByText("Purchasing")).toBeInTheDocument(); // process name
  expect(within(planRows[0]!).getByText("Mara Quality")).toBeInTheDocument(); // lead via directory
  expect(within(planRows[0]!).getByText("FRM-AUD-002")).toBeInTheDocument();
});

test("Add plan POSTs to the selected program (date + process + checklist ref)", async () => {
  grant(["audit.plan"]);
  let body: Record<string, unknown> | null = null;
  let target = "";
  server.use(
    http.post("/api/v1/audit-programs/:id/plans", async ({ request, params }) => {
      target = String(params.id);
      body = (await request.json()) as typeof body;
      return HttpResponse.json(
        {
          id: "pl-new-00-0000-0000-0000-000000000000",
          program_id: target,
          auditee_process_id: null,
          lead_auditor_user_id: null,
          scheduled_date: "2026-11-01",
          checklist_ref: "FRM-AUD-002",
          created_at: "2026-06-09T09:00:00+00:00",
        },
        { status: 201 },
      );
    }),
  );
  const u = userEvent.setup();
  renderWithProviders(<ProgramPage />, { route: "/audits/program" });
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

test("an archived selected program hides Add plan; a racing 409 surfaces calmly", async () => {
  grant(["audit.plan"]);
  const u = userEvent.setup();
  renderWithProviders(<ProgramPage />, { route: "/audits/program" });
  // Select the archived program → no Add plan.
  await u.click(await screen.findByRole("button", { name: /^Select program AUDPROG-000002:/ }));
  expect(screen.queryByRole("button", { name: /Add plan/ })).toBeNull();
  // Back on the active one, a server 409 (race: archived elsewhere) renders calmly in the modal.
  await u.click(screen.getByRole("button", { name: /^Select program AUDPROG-000001:/ }));
  server.use(
    http.post("/api/v1/audit-programs/:id/plans", () =>
      HttpResponse.json(
        { code: "program_archived", title: "Cannot add a plan to an archived program" },
        { status: 409 },
      ),
    ),
  );
  await u.click(await screen.findByRole("button", { name: /Add plan/ }));
  const dialog = await screen.findByRole("dialog");
  await u.click(within(dialog).getByRole("button", { name: /Save plan/ }));
  expect(
    await within(dialog).findByText(/Cannot add a plan to an archived program/),
  ).toBeInTheDocument();
});

test("the process picker is omitted when GET /processes 403s (degrade)", async () => {
  grant(["audit.plan"]);
  server.use(
    http.get("/api/v1/processes", () =>
      HttpResponse.json({ code: "permission_denied", title: "Forbidden" }, { status: 403 }),
    ),
  );
  const u = userEvent.setup();
  renderWithProviders(<ProgramPage />, { route: "/audits/program" });
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
      return HttpResponse.json({
        id: String(params.id),
        identifier: "AUDPROG-000001",
        title: "2026 Internal Audit Program",
        period: null,
        coverage: null,
        archived: false,
        created_at: "2026-01-05T09:00:00+00:00",
      });
    }),
  );
  const u = userEvent.setup();
  renderWithProviders(<ProgramPage />, { route: "/audits/program" });
  const row = await screen.findByRole("row", { name: /AUDPROG-000001/ });
  await u.click(within(row).getByRole("button", { name: /Edit/ }));
  const dialog = await screen.findByRole("dialog");
  await u.clear(within(dialog).getByLabelText(/Period/));
  await u.click(within(dialog).getByRole("button", { name: /Save program/ }));
  await waitFor(() => expect(body).not.toBeNull());
  expect(body!["period"]).toBe("");
});
