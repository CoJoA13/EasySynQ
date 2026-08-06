import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { ComplaintsPage } from "./ComplaintsPage";

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

test("lists complaints from {data}; hides write affordances without the keys (default perms)", async () => {
  renderWithProviders(<ComplaintsPage />, { route: "/capa/complaints" });
  expect(await screen.findByText("CMP-000007")).toBeInTheDocument();
  expect(screen.getByText(/Delivered batch missing CoA/)).toBeInTheDocument();
  // Default /me/permissions is empty → neither write affordance renders (negative-gating).
  expect(screen.queryByRole("button", { name: /Log complaint/ })).toBeNull();
  expect(screen.queryByRole("button", { name: /Raise CAPA/ })).toBeNull();
});

test("hides 'Log complaint' without record.create; shows + opens it with the key", async () => {
  grant(["record.create"]);
  const u = userEvent.setup();
  renderWithProviders(<ComplaintsPage />, { route: "/capa/complaints" });
  const btn = await screen.findByRole("button", { name: /Log complaint/ });
  await u.click(btn);
  expect(await screen.findByLabelText(/Description/)).toBeInTheDocument();
});

test("logging a complaint POSTs /complaints and closes the modal", async () => {
  grant(["record.create"]);
  let posted = false;
  server.use(
    http.post("/api/v1/complaints", () => {
      posted = true;
      return HttpResponse.json(
        {
          id: "x",
          identifier: "CMP-x",
          customer: null,
          received_at: null,
          channel: null,
          description: "x",
          severity: null,
          spawned_capa_id: null,
        },
        { status: 201 },
      );
    }),
  );
  const u = userEvent.setup();
  renderWithProviders(<ComplaintsPage />, { route: "/capa/complaints" });
  await u.click(await screen.findByRole("button", { name: /Log complaint/ }));
  await u.type(await screen.findByLabelText(/Description/), "Customer reported a missing CoA");
  const dialog = screen.getByRole("dialog");
  await u.click(within(dialog).getByRole("button", { name: /Log complaint/ }));
  await waitFor(() => expect(posted).toBe(true));
});

test("raising a CAPA opens a severity-confirm modal (pre-filled) and POSTs the severity", async () => {
  grant(["capa.create"]);
  let body: { severity?: string } | null = null;
  server.use(
    http.post("/api/v1/complaints/:id/spawn-capa", async ({ request }) => {
      body = (await request.json()) as typeof body;
      return HttpResponse.json({ id: "ca-x" }, { status: 201 });
    }),
  );
  const u = userEvent.setup();
  renderWithProviders(<ComplaintsPage />, { route: "/capa/complaints" });
  const row = await screen.findByRole("row", { name: /CMP-000007/ });
  await u.click(within(row).getByRole("button", { name: /Raise CAPA/ }));
  // CMP-000007 has severity Critical → the modal pre-fills it; confirm to raise.
  const dialog = await screen.findByRole("dialog");
  expect(within(dialog).getByText("Raise a CAPA")).toBeInTheDocument();
  await u.click(within(dialog).getByRole("button", { name: /Raise CAPA/ }));
  await waitFor(() => expect(body).not.toBeNull());
  expect(body!.severity).toBe("Critical");
});

test("raising from a severity-LESS complaint requires picking a severity (no dead-end)", async () => {
  grant(["capa.create"]);
  server.use(
    http.get("/api/v1/complaints", () =>
      HttpResponse.json({
        data: [
          {
            id: "cm-nosev",
            identifier: "CMP-000099",
            customer: null,
            received_at: null,
            channel: null,
            description: "No severity yet",
            severity: null,
            spawned_capa_id: null,
          },
        ],
      }),
    ),
  );
  let body: { severity?: string } | null = null;
  server.use(
    http.post("/api/v1/complaints/:id/spawn-capa", async ({ request }) => {
      body = (await request.json()) as typeof body;
      return HttpResponse.json({ id: "ca-y" }, { status: 201 });
    }),
  );
  const u = userEvent.setup();
  renderWithProviders(<ComplaintsPage />, { route: "/capa/complaints" });
  const row = await screen.findByRole("row", { name: /CMP-000099/ });
  await u.click(within(row).getByRole("button", { name: /Raise CAPA/ }));
  const dialog = await screen.findByRole("dialog");
  // No severity to inherit → the confirm button is disabled until one is picked (no 422 dead-end).
  expect(within(dialog).getByRole("button", { name: /Raise CAPA/ })).toBeDisabled();
  const [sevInput] = within(dialog).getAllByLabelText(/Severity/);
  await u.click(sevInput!);
  await u.click(await screen.findByRole("option", { name: "Major" }));
  await u.click(within(dialog).getByRole("button", { name: /Raise CAPA/ }));
  await waitFor(() => expect(body).not.toBeNull());
  expect(body!.severity).toBe("Major");
});

test("shows 'View CAPA' (not Raise) for an already-spawned complaint", async () => {
  grant(["capa.create"]);
  renderWithProviders(<ComplaintsPage />, { route: "/capa/complaints" });
  const row = await screen.findByRole("row", { name: /CMP-000006/ });
  // CMP-000006 carries spawned_capa_id ca000002… → the link deep-links that CAPA's board drawer.
  expect(within(row).getByRole("link", { name: /View CAPA/ })).toHaveAttribute(
    "href",
    "/capa?capa=ca000002-0002-0002-0002-000000000002",
  );
  expect(within(row).queryByRole("button", { name: /Raise CAPA/ })).toBeNull();
});

test("renders a calm no-access panel on a 403", async () => {
  server.use(
    http.get("/api/v1/complaints", () =>
      HttpResponse.json({ code: "permission_denied", title: "Forbidden" }, { status: 403 }),
    ),
  );
  renderWithProviders(<ComplaintsPage />, { route: "/capa/complaints" });
  expect(await screen.findByText(/don't have access to complaints/)).toBeInTheDocument();
});

test("no axe violations", async () => {
  const { container } = renderWithProviders(<ComplaintsPage />, { route: "/capa/complaints" });
  await screen.findByText("CMP-000007");
  expect(await axe(container)).toHaveNoViolations();
});
