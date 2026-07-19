import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { NcrsPage } from "./NcrsPage";

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

test("lists NCRs from {data} with friendly source labels", async () => {
  renderWithProviders(<NcrsPage />, { route: "/capa/ncrs" });
  expect(await screen.findByText("NCR-000052")).toBeInTheDocument();
  const row = screen.getByRole("row", { name: /NCR-000052/ });
  expect(within(row).getByText("Process")).toBeInTheDocument();
  // Default /me/permissions is empty → no write affordances (negative-gating); the undisposed row
  // shows "Pending", not a "Record disposition" button.
  expect(screen.queryByRole("button", { name: /Raise NCR/ })).toBeNull();
  expect(within(row).queryByRole("button", { name: /Record disposition/ })).toBeNull();
  expect(within(row).getByText("Pending")).toBeInTheDocument();
});

test("hides 'Raise NCR' without ncr.create; shows + opens it with the key", async () => {
  grant(["ncr.create"]);
  const u = userEvent.setup();
  renderWithProviders(<NcrsPage />, { route: "/capa/ncrs" });
  await u.click(await screen.findByRole("button", { name: /Raise NCR/ }));
  expect(await screen.findByLabelText(/^Source/)).toBeInTheDocument();
});

test("a disposed NCR shows its disposition + who/when read-only (no action button)", async () => {
  grant(["ncr.record_correction"]);
  renderWithProviders(<NcrsPage />, { route: "/capa/ncrs" });
  const row = await screen.findByRole("row", { name: /NCR-000049/ });
  expect(within(row).getByText("Rework")).toBeInTheDocument();
  expect(within(row).queryByRole("button", { name: /Record disposition/ })).toBeNull();
  // who/when audit context (the table is the only NCR surface) — authorizer resolved via the directory.
  expect(await within(row).findByText(/Mara Quality/)).toBeInTheDocument();
  expect(within(row).getByText(/2026-06-04/)).toBeInTheDocument();
});

test("records a disposition (PATCH) for an undisposed NCR", async () => {
  grant(["ncr.record_correction"]);
  let patched = false;
  server.use(
    http.patch("/api/v1/ncrs/:id/disposition", () => {
      patched = true;
      return HttpResponse.json({
        id: "nc000001-0001-0001-0001-000000000001",
        identifier: "NCR-000052",
        source: "process",
        description: "x",
        severity: "Major",
        process_id: null,
        disposition: "scrap",
        disposition_authorized_by: null,
        disposition_notes: null,
        disposed_at: "2026-06-09T00:00:00+00:00",
        created_at: "2026-06-03T09:00:00+00:00",
      });
    }),
  );
  const u = userEvent.setup();
  renderWithProviders(<NcrsPage />, { route: "/capa/ncrs" });
  const row = await screen.findByRole("row", { name: /NCR-000052/ });
  await u.click(within(row).getByRole("button", { name: /Record disposition/ }));
  const dialog = screen.getByRole("dialog");
  const [dispInput] = within(dialog).getAllByLabelText(/Disposition/);
  await u.click(dispInput!);
  await u.click(await screen.findByRole("option", { name: "Scrap" }));
  await u.click(within(dialog).getByRole("button", { name: /Record disposition/ }));
  await waitFor(() => expect(patched).toBe(true));
});

test("a one-shot 409 (already dispositioned) is surfaced calmly", async () => {
  grant(["ncr.record_correction"]);
  server.use(
    http.patch("/api/v1/ncrs/:id/disposition", () =>
      HttpResponse.json(
        { code: "ncr_already_dispositioned", title: "Already dispositioned" },
        { status: 409 },
      ),
    ),
  );
  const u = userEvent.setup();
  renderWithProviders(<NcrsPage />, { route: "/capa/ncrs" });
  const row = await screen.findByRole("row", { name: /NCR-000052/ });
  await u.click(within(row).getByRole("button", { name: /Record disposition/ }));
  const dialog = screen.getByRole("dialog");
  const [dispInput] = within(dialog).getAllByLabelText(/Disposition/);
  await u.click(dispInput!);
  await u.click(await screen.findByRole("option", { name: "Scrap" }));
  await u.click(within(dialog).getByRole("button", { name: /Record disposition/ }));
  expect(await screen.findByText(/Already dispositioned/)).toBeInTheDocument();
});

test("a 409 race refetches the list so the row flips to its disposed read-only state", async () => {
  grant(["ncr.record_correction"]);
  let raced = false; // another user won the race: our PATCH 409s, but the server is now disposed.
  server.use(
    http.get("/api/v1/ncrs", () =>
      HttpResponse.json({
        data: [
          {
            id: "nc000001-0001-0001-0001-000000000001",
            identifier: "NCR-000052",
            source: "process",
            description: "Torque out of spec",
            severity: "Major",
            process_id: null,
            disposition: raced ? "scrap" : null,
            disposition_authorized_by: null,
            disposition_notes: null,
            disposed_at: raced ? "2026-06-09T00:00:00+00:00" : null,
            created_at: "2026-06-03T09:00:00+00:00",
          },
        ],
      }),
    ),
    http.patch("/api/v1/ncrs/:id/disposition", () => {
      raced = true;
      return HttpResponse.json(
        { code: "ncr_already_dispositioned", title: "Already dispositioned" },
        { status: 409 },
      );
    }),
  );
  const u = userEvent.setup();
  renderWithProviders(<NcrsPage />, { route: "/capa/ncrs" });
  const row = await screen.findByRole("row", { name: /NCR-000052/ });
  await u.click(within(row).getByRole("button", { name: /Record disposition/ }));
  const dialog = screen.getByRole("dialog");
  const [dispInput] = within(dialog).getAllByLabelText(/Disposition/);
  await u.click(dispInput!);
  await u.click(await screen.findByRole("option", { name: "Scrap" }));
  await u.click(within(dialog).getByRole("button", { name: /Record disposition/ }));
  // the calm 409 message shows AND the invalidate-on-settle refetch flips the row to read-only — the
  // stale "Record disposition" action is gone (asserting the button, not "Scrap", which the open
  // modal's Select also shows).
  expect(await screen.findByText(/Already dispositioned/)).toBeInTheDocument();
  await waitFor(() =>
    expect(
      within(screen.getByRole("row", { name: /NCR-000052/ })).queryByRole("button", {
        name: /Record disposition/,
      }),
    ).toBeNull(),
  );
});

test("renders a calm no-access panel on a 403", async () => {
  server.use(
    http.get("/api/v1/ncrs", () =>
      HttpResponse.json({ code: "forbidden", title: "Forbidden" }, { status: 403 }),
    ),
  );
  renderWithProviders(<NcrsPage />, { route: "/capa/ncrs" });
  expect(await screen.findByText(/don't have access to NCRs/)).toBeInTheDocument();
});

test("no axe violations", async () => {
  const { container } = renderWithProviders(<NcrsPage />, { route: "/capa/ncrs" });
  await screen.findByText("NCR-000052");
  expect(await axe(container)).toHaveNoViolations();
});

test("gives register headers a column scope (a11y)", async () => {
  renderWithProviders(<NcrsPage />, { route: "/capa/ncrs" });
  const header = await screen.findByRole("columnheader", { name: "Identifier" });
  expect(header).toHaveAttribute("scope", "col");
});
