import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { http, HttpResponse } from "msw";
import { useLocation } from "react-router-dom";
import { expect, test } from "vitest";
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

test("a list-view row opens the drawer via keyboard (Enter), not mouse-only", async () => {
  renderWithProviders(<CapaBoardPage />, { route: "/capa" });
  await screen.findByText(/Supplier re-evaluation/);
  await userEvent.click(screen.getByRole("radio", { name: "List" }));
  const row = await screen.findByRole("row", { name: /REC-000031/ });
  row.focus();
  await userEvent.keyboard("{Enter}");
  expect(await screen.findByText("Closed-loop thread")).toBeInTheDocument();
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
      HttpResponse.json({ code: "forbidden", title: "Forbidden" }, { status: 403 }),
    ),
  );
  renderWithProviders(<CapaBoardPage />, { route: "/capa" });
  expect(await screen.findByText(/don't have access/)).toBeInTheDocument();
});

test("no axe violations", async () => {
  const { container } = renderWithProviders(<CapaBoardPage />, { route: "/capa" });
  await screen.findByText(/Supplier re-evaluation/);
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
  const raise = await screen.findByRole("button", { name: /Raise CAPA/ });
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
  // The required picker renders with Mantine's asterisk (prefix match), NOT the optional label.
  expect(await screen.findByLabelText(/^Process/)).toBeInTheDocument();
  expect(screen.queryByLabelText("Process (optional)")).toBeNull();
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
