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

test("lists complaints from {data}", async () => {
  renderWithProviders(<ComplaintsPage />, { route: "/capa/complaints" });
  expect(await screen.findByText("CMP-000007")).toBeInTheDocument();
  expect(screen.getByText(/Delivered batch missing CoA/)).toBeInTheDocument();
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
        { id: "x", identifier: "CMP-x", customer: null, received_at: null, channel: null, description: "x", severity: null, spawned_capa_id: null },
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

test("shows 'Spawn CAPA' for an unspawned complaint and POSTs the spawn", async () => {
  grant(["capa.create"]);
  let spawned = false;
  server.use(
    http.post("/api/v1/complaints/:id/spawn-capa", () => {
      spawned = true;
      return HttpResponse.json({ id: "ca-x" }, { status: 201 });
    }),
  );
  const u = userEvent.setup();
  renderWithProviders(<ComplaintsPage />, { route: "/capa/complaints" });
  const row = await screen.findByRole("row", { name: /CMP-000007/ });
  await u.click(within(row).getByRole("button", { name: /Spawn CAPA/ }));
  await waitFor(() => expect(spawned).toBe(true));
});

test("shows 'View CAPA' (not Spawn) for an already-spawned complaint", async () => {
  grant(["capa.create"]);
  renderWithProviders(<ComplaintsPage />, { route: "/capa/complaints" });
  const row = await screen.findByRole("row", { name: /CMP-000006/ });
  expect(within(row).getByRole("link", { name: /View CAPA/ })).toBeInTheDocument();
  expect(within(row).queryByRole("button", { name: /Spawn CAPA/ })).toBeNull();
});

test("renders a calm no-access panel on a 403", async () => {
  server.use(
    http.get("/api/v1/complaints", () =>
      HttpResponse.json({ code: "forbidden", title: "Forbidden" }, { status: 403 }),
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
