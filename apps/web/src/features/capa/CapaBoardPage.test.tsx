import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { http, HttpResponse } from "msw";
import { useLocation } from "react-router-dom";
import { expect, test } from "vitest";
import { TONE_GLYPH } from "../../lib/status";
import type { MePermissions } from "../../lib/types";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { CapaBoardPage } from "./CapaBoardPage";

function LocationProbe() {
  const loc = useLocation();
  return <div data-testid="loc">{loc.pathname + loc.search}</div>;
}

test("groups CAPAs into lifecycle columns (ActionPlan+Implement merge; Rejected in Closed)", async () => {
  renderWithProviders(<CapaBoardPage />, { route: "/capa" });
  const action = await screen.findByRole("group", { name: "Action" });
  expect(within(action).getByText(/Scrap-rate spike/)).toBeInTheDocument();
  const closed = screen.getByRole("group", { name: "Closed" });
  expect(within(closed).getByText(/Duplicate complaint/)).toBeInTheDocument();
});

test("the Open tile counts non-terminal CAPAs and by-source breaks down", async () => {
  renderWithProviders(<CapaBoardPage />, { route: "/capa" });
  expect(await screen.findByText("5")).toBeInTheDocument();
  expect(screen.getByText("Audit · 3")).toBeInTheDocument();
});

test("the summary row carries overdue and a severity histogram, not just open + source", async () => {
  renderWithProviders(<CapaBoardPage />, { route: "/capa" });
  // `overdue` is server-computed and already false for Closed/Rejected, so the tile is a plain
  // count of the flag — one row in the fixture carries it.
  // Scoped to the tile: the kanban column headers each carry a count badge, so a bare
  // getByText("1") matches several elements and would not pin the number to Overdue at all.
  const overdueTile = (await screen.findByText("Overdue")).closest(".mantine-Card-root");
  expect(overdueTile).not.toBeNull();
  expect(within(overdueTile as HTMLElement).getByText("1")).toBeInTheDocument();
  // The danger glyph is the non-colour channel (DP-7): with a non-zero overdue count the tile must
  // still read as bad news when the colour is stripped.
  expect(within(overdueTile as HTMLElement).getByText(TONE_GLYPH.danger)).toBeInTheDocument();

  // The histogram rides the canonical severity pill (SEVERITY_TONE via SeverityBadge), NOT a second
  // grey badge that would reintroduce the ad-hoc colour map S-statusbadge-2 removed. Appending the
  // count also keeps these accessible names distinct from the per-card pills on the same board.
  expect(screen.getByLabelText("Severity: Critical · 1")).toBeInTheDocument();
  expect(screen.getByLabelText("Severity: Major · 3")).toBeInTheDocument();
  expect(screen.getByLabelText("Severity: Minor · 3")).toBeInTheDocument();
});

test("filtering by severity narrows the cards", async () => {
  renderWithProviders(<CapaBoardPage />, { route: "/capa" });
  await screen.findByText(/Supplier re-evaluation/);
  // Mantine v7 Select renders a readonly input + a listbox both with aria-label="Severity".
  // getAllByLabelText returns [input, listbox]; click the input (index 0) to open the dropdown.
  const [severityInput] = screen.getAllByLabelText("Severity");
  await userEvent.click(severityInput!);
  await userEvent.click(await screen.findByRole("option", { name: "Critical" }));
  expect(screen.getByText(/Delivered batch missing CoA/)).toBeInTheDocument();
  expect(screen.queryByText(/Supplier re-evaluation/)).toBeNull();
});

test("opening a card shows the detail drawer", async () => {
  renderWithProviders(<CapaBoardPage />, { route: "/capa" });
  await userEvent.click(await screen.findByRole("button", { name: /REC-000031/ }));
  expect(await screen.findByText("Closed-loop thread")).toBeInTheDocument();
});

test("CAPA list rows are structural and expose a named native primary button", async () => {
  const u = userEvent.setup();
  renderWithProviders(<CapaBoardPage />, { route: "/capa" });
  await u.click(await screen.findByRole("radio", { name: "List" }));
  const row = screen.getByRole("row", { name: /REC-000031/ });
  expect(row).not.toHaveAttribute("tabindex");
  expect(
    within(row).getByRole("button", {
      name: "Open CAPA REC-000031: Supplier re-evaluation overdue for 2 vendors",
    }),
  ).toBeInTheDocument();
});

test.each(["{Enter}", " "])("the native CAPA control opens the drawer with %s", async (key) => {
  const u = userEvent.setup();
  renderWithProviders(<CapaBoardPage />, { route: "/capa" });
  await u.click(await screen.findByRole("radio", { name: "List" }));
  const open = screen.getByRole("button", { name: /^Open CAPA REC-000031:/ });
  open.focus();
  await u.keyboard(key);
  expect(await screen.findByText("Closed-loop thread")).toBeInTheDocument();
});

test("ArrowDown moves to the next CAPA control without opening it", async () => {
  const u = userEvent.setup();
  renderWithProviders(<CapaBoardPage />, { route: "/capa" });
  await u.click(await screen.findByRole("radio", { name: "List" }));
  const controls = screen.getAllByRole("button", { name: /^Open CAPA / });
  controls[0]!.focus();
  await u.keyboard("{ArrowDown}");
  expect(controls[1]).toHaveFocus();
  expect(screen.queryByText("Closed-loop thread")).toBeNull();
});

test("clicking ordinary CAPA cell content does not open the drawer", async () => {
  const u = userEvent.setup();
  renderWithProviders(<CapaBoardPage />, { route: "/capa" });
  await u.click(await screen.findByRole("radio", { name: "List" }));
  const row = screen.getByRole("row", { name: /REC-000031/ });
  await u.click(within(row).getByText("Audit"));
  expect(screen.queryByText("Closed-loop thread")).toBeNull();
});

test("deep-links the detail drawer open from ?capa=<id> on mount", async () => {
  // ca000008 maps to the close-ready fixture (REC-000040 / "Press guard interlock bypass"), which is
  // NOT in the board list — so its appearance proves the drawer opened for exactly that id, not a card.
  renderWithProviders(<CapaBoardPage />, {
    route: "/capa?capa=ca000008-0008-0008-0008-000000000008",
  });
  expect(await screen.findByText("Press guard interlock bypass")).toBeInTheDocument();
  expect(screen.getByText("REC-000040")).toBeInTheDocument();
  expect(screen.getByText("Closed-loop thread")).toBeInTheDocument();
});

test("closing the deep-linked drawer clears the ?capa param", async () => {
  const u = userEvent.setup();
  renderWithProviders(
    <>
      <CapaBoardPage />
      <LocationProbe />
    </>,
    { route: "/capa?capa=ca000008-0008-0008-0008-000000000008" },
  );
  await screen.findByText("Press guard interlock bypass");
  expect(screen.getByTestId("loc")).toHaveTextContent("capa=ca000008");
  await u.keyboard("{Escape}");
  await waitFor(() => expect(screen.queryByText("Press guard interlock bypass")).toBeNull());
  expect(screen.getByTestId("loc")).not.toHaveTextContent("capa=");
});

test("renders a calm no-access panel on a 403", async () => {
  server.use(
    http.get("/api/v1/capas", () =>
      HttpResponse.json({ code: "permission_denied", title: "Forbidden" }, { status: 403 }),
    ),
  );
  renderWithProviders(<CapaBoardPage />, { route: "/capa" });
  expect(await screen.findByText(/don't have access/)).toBeInTheDocument();
});

test("no axe violations in List view", async () => {
  const u = userEvent.setup();
  const { container } = renderWithProviders(<CapaBoardPage />, { route: "/capa" });
  await u.click(await screen.findByRole("radio", { name: "List" }));
  expect(await axe(container)).toHaveNoViolations();
});

test("shows the Raise CAPA button when the caller holds capa.create and opens the modal", async () => {
  server.use(
    http.get("/api/v1/me/permissions", () =>
      HttpResponse.json({
        scope: { level: "SYSTEM", selector: null },
        permissions: [{ key: "capa.create", effect: "ALLOW", source: null }],
      }),
    ),
  );
  const u = userEvent.setup();
  renderWithProviders(<CapaBoardPage />, { route: "/capa" });
  const raise = await screen.findByRole("button", { name: "Raise CAPA" });
  await u.click(raise);
  expect(await screen.findByLabelText(/^Title/)).toBeInTheDocument();
});

// A bound Process-Owner holds capa.create ONLY at their owned process scope, never at SYSTEM. The
// board probes capa.create at the first readable process (GET /processes → Purchasing first), so the
// PROCESS-scoped grant surfaces the Raise affordance the SYSTEM-only probe would have hidden.
test("shows the Raise CAPA button for a purely-PROCESS-scoped capa.create holder", async () => {
  server.use(
    http.get("/api/v1/me/permissions", ({ request }) => {
      const level = new URL(request.url).searchParams.get("scope_level");
      return HttpResponse.json({
        scope: { level: level ?? "SYSTEM", selector: null },
        permissions:
          level === "PROCESS" ? [{ key: "capa.create", effect: "ALLOW", source: null }] : [],
      } satisfies MePermissions);
    }),
  );
  renderWithProviders(<CapaBoardPage />, { route: "/capa" });
  expect(await screen.findByRole("button", { name: /Raise CAPA/ })).toBeInTheDocument();
});

// The board threads requireProcess=!systemCanCreate into the modal: a PROCESS-only creator (no SYSTEM
// capa.create) gets a REQUIRED process picker ("Process"), not the optional one — a process-less raise
// would 403 at the server's SYSTEM-scope enforce.
test("opens the Raise modal with a required process picker for a PROCESS-only creator", async () => {
  server.use(
    http.get("/api/v1/me/permissions", ({ request }) => {
      const level = new URL(request.url).searchParams.get("scope_level");
      return HttpResponse.json({
        scope: { level: level ?? "SYSTEM", selector: null },
        permissions:
          level === "PROCESS" ? [{ key: "capa.create", effect: "ALLOW", source: null }] : [],
      } satisfies MePermissions);
    }),
  );
  const u = userEvent.setup();
  renderWithProviders(<CapaBoardPage />, { route: "/capa" });
  await u.click(await screen.findByRole("button", { name: /Raise CAPA/ }));
  // The required placeholder is set ONLY when requireProcess=true — a genuine discriminator that the
  // board threaded requireProcess=!systemCanCreate (an optional picker carries no placeholder). The
  // submit-gate MECHANICS are covered by the RaiseCapaModal test; here we pin the board's threading.
  expect(await screen.findByPlaceholderText("Pick the owning process")).toBeInTheDocument();
  expect(screen.queryByLabelText("Process (optional)")).toBeNull();
});

// The modal is conditionally mounted, so closing it unmounts + discards the draft — a picked-then-
// cancelled field must not bleed into the next raise (the RaiseInitiativeModal precedent).
test("re-opening the Raise modal discards the previous draft", async () => {
  server.use(
    http.get("/api/v1/me/permissions", () =>
      HttpResponse.json({
        scope: { level: "SYSTEM", selector: null },
        permissions: [{ key: "capa.create", effect: "ALLOW", source: null }],
      } satisfies MePermissions),
    ),
  );
  const u = userEvent.setup();
  renderWithProviders(<CapaBoardPage />, { route: "/capa" });
  await u.click(await screen.findByRole("button", { name: /Raise CAPA/ }));
  await u.type(await screen.findByLabelText(/^Title/), "Stale draft");
  await u.click(screen.getByRole("button", { name: "Cancel" }));
  // Re-open: the Title is empty again (the modal remounted fresh, not the stale draft).
  await u.click(await screen.findByRole("button", { name: /Raise CAPA/ }));
  expect(await screen.findByLabelText(/^Title/)).toHaveValue("");
});

// The gate must key on capa.CREATE, never on "has readable processes": an Internal Auditor holds
// SYSTEM capa.read (board access) + SYSTEM process.read (a non-empty process list) but no capa.create
// at ANY scope — they must NOT see the Raise button.
test("hides the Raise CAPA button for a read-only caller with no capa.create", async () => {
  server.use(
    http.get("/api/v1/me/permissions", () =>
      HttpResponse.json({
        scope: { level: "SYSTEM", selector: null },
        permissions: [{ key: "capa.read", effect: "ALLOW", source: null }],
      } satisfies MePermissions),
    ),
  );
  renderWithProviders(<CapaBoardPage />, { route: "/capa" });
  await screen.findByText(/Supplier re-evaluation/);
  expect(screen.queryByRole("button", { name: /Raise CAPA/ })).toBeNull();
});
