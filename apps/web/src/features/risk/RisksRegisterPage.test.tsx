import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { axe } from "jest-axe";
import { expect, it } from "vitest";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import { RisksRegisterPage } from "./RisksRegisterPage";

function grant(...keys: string[]) {
  server.use(
    http.get("/api/v1/me/permissions", () =>
      HttpResponse.json({
        scope: { level: "SYSTEM", selector: null },
        permissions: keys.map((key) => ({ key, effect: "ALLOW", source: "test" })),
      }),
    ),
  );
}

it("renders the matrix, scorecard, and a row per risk with a band badge", async () => {
  const { container } = renderWithProviders(<RisksRegisterPage />, { route: "/risks" });
  await waitFor(() =>
    expect(screen.getByText("Supplier single point of failure")).toBeInTheDocument(),
  );
  // scorecard rollup (client-side from the live rows): 2 of 4 high or critical
  expect(screen.getByText(/2 of 4 high or critical/i)).toBeInTheDocument();
  // the matrix carries an a11y summary
  expect(
    screen.getByRole("img", { name: /risk matrix.*4 risks plotted; 2 high or critical/i }),
  ).toBeInTheDocument();
  // a row's band pill is the canonical StatusBadge (meaning label, never a colour word)
  const row = screen.getByText("Untrained operators on the new line").closest("tr")!;
  expect(within(row).getByText("High")).toBeInTheDocument();
  expect(await axe(container)).toHaveNoViolations();
});

it("shows the read-only banner and hides New when the register is Effective", async () => {
  // default fixture state = Effective → read-only, no steward UI
  grant("register.manage"); // even WITH manage, an Effective head hides New (head not editable)
  renderWithProviders(<RisksRegisterPage />, { route: "/risks" });
  await waitFor(() =>
    expect(screen.getByText(/this register is effective \(read-only\)/i)).toBeInTheDocument(),
  );
  expect(screen.queryByRole("button", { name: "New risk" })).not.toBeInTheDocument();
});

it("shows New (and opens the create modal) when editable + register.manage", async () => {
  server.use(
    http.get("/api/v1/risks/register", () =>
      HttpResponse.json({
        exists: true,
        register_doc_id: "d0c00000-0000-0000-0000-0000000000aa",
        identifier: "RSK-001",
        state: "UnderRevision",
        current_effective_version_id: "ve000001-0001-0001-0001-000000000001",
        has_governing: true,
      }),
    ),
  );
  grant("register.manage");
  const user = userEvent.setup();
  renderWithProviders(<RisksRegisterPage />, { route: "/risks" });
  await waitFor(() =>
    expect(screen.getByText("Supplier single point of failure")).toBeInTheDocument(),
  );
  await user.click(screen.getByRole("button", { name: "New risk" }));
  expect(await screen.findByRole("dialog")).toBeInTheDocument();
  expect(screen.getByRole("dialog")).toHaveTextContent(/new risk or opportunity/i);
});

it("shows New for a PROCESS-only register.manage holder (the first-readable-process probe)", async () => {
  // editable head + a scope-aware /me/permissions: register.manage ONLY at PROCESS scope (empty at
  // SYSTEM) — so canCreate must come from the first-readable-process probe, not a SYSTEM grant. This
  // exercises the gate's PROCESS branch + requireProcess (the picker becomes required).
  server.use(
    http.get("/api/v1/risks/register", () =>
      HttpResponse.json({
        exists: true,
        register_doc_id: "d0c00000-0000-0000-0000-0000000000aa",
        identifier: "RSK-001",
        state: "UnderRevision",
        current_effective_version_id: null,
        has_governing: false,
      }),
    ),
    http.get("/api/v1/me/permissions", ({ request }) => {
      const level = new URL(request.url).searchParams.get("scope_level");
      return HttpResponse.json({
        scope: { level: level ?? "SYSTEM", selector: null },
        permissions:
          level === "PROCESS" ? [{ key: "register.manage", effect: "ALLOW", source: "test" }] : [],
      });
    }),
  );
  const user = userEvent.setup();
  renderWithProviders(<RisksRegisterPage />, { route: "/risks" });
  await waitFor(() =>
    expect(screen.getByText("Supplier single point of failure")).toBeInTheDocument(),
  );
  await user.click(screen.getByRole("button", { name: "New risk" }));
  const dialog = await screen.findByRole("dialog");
  // requireProcess (a PROCESS-only creator) → the picker is required; query by its placeholder, not
  // the asterisked label (the S-capa-raise-process MAJOR).
  expect(within(dialog).getByPlaceholderText("Pick the owning process")).toBeInTheDocument();
});

it("the band filter narrows the table (matrix + scorecard stay whole)", async () => {
  const user = userEvent.setup();
  renderWithProviders(<RisksRegisterPage />, { route: "/risks" });
  await waitFor(() =>
    expect(screen.getByText("Supplier single point of failure")).toBeInTheDocument(),
  );
  await user.click(screen.getByRole("radio", { name: "High" }));
  // only the high row remains in the table; the critical/medium/low descriptions are gone
  expect(screen.getByText("Untrained operators on the new line")).toBeInTheDocument();
  expect(screen.queryByText("Supplier single point of failure")).not.toBeInTheDocument();
  expect(screen.queryByText("Automate the inspection step")).not.toBeInTheDocument();
  // the scorecard still reflects the whole register
  expect(screen.getByText(/2 of 4 high or critical/i)).toBeInTheDocument();
});

it("sorting by Band is danger-first on the default (desc) click, not low-first", async () => {
  const user = userEvent.setup();
  renderWithProviders(<RisksRegisterPage />, { route: "/risks" });
  await waitFor(() =>
    expect(screen.getByText("Supplier single point of failure")).toBeInTheDocument(),
  );
  await user.click(screen.getByRole("button", { name: "Sort by Band" }));
  // first Band click uses the table's default desc → must surface Critical before Low (the negated
  // band_rank fix; a raw band_rank desc would put Low first).
  const critical = screen.getByText("Supplier single point of failure"); // critical row
  const low = screen.getByText("Minor labelling drift"); // low row
  expect(critical.compareDocumentPosition(low) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  // toggle → asc → best-first (Low before Critical)
  await user.click(screen.getByRole("button", { name: "Sort by Band" }));
  expect(low.compareDocumentPosition(critical) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
});

it("the type filter narrows to opportunities", async () => {
  const user = userEvent.setup();
  renderWithProviders(<RisksRegisterPage />, { route: "/risks" });
  await waitFor(() =>
    expect(screen.getByText("Supplier single point of failure")).toBeInTheDocument(),
  );
  await user.click(screen.getByRole("radio", { name: "Opportunities" }));
  expect(screen.getByText("Automate the inspection step")).toBeInTheDocument();
  expect(screen.queryByText("Supplier single point of failure")).not.toBeInTheDocument();
});

it("a ?risk= deep-link opens the detail drawer", async () => {
  renderWithProviders(<RisksRegisterPage />, {
    route: "/risks?risk=ab000002-0002-0002-0002-000000000002",
  });
  const dialog = await screen.findByRole("dialog");
  expect(within(dialog).getByText("Untrained operators on the new line")).toBeInTheDocument();
});

it("opens the drawer from a row anchor", async () => {
  const user = userEvent.setup();
  renderWithProviders(<RisksRegisterPage />, { route: "/risks" });
  await waitFor(() =>
    expect(screen.getByText("Supplier single point of failure")).toBeInTheDocument(),
  );
  await user.click(screen.getByText("Supplier single point of failure"));
  const dialog = await screen.findByRole("dialog");
  expect(within(dialog).getByText(/likelihood 4 × severity 5 = rating 20/i)).toBeInTheDocument();
});

it("shows an empty state when there are no risks", async () => {
  server.use(http.get("/api/v1/risks", () => HttpResponse.json({ data: [] })));
  renderWithProviders(<RisksRegisterPage />, { route: "/risks" });
  await waitFor(() =>
    expect(screen.getByText(/no risks or opportunities yet/i)).toBeInTheDocument(),
  );
});
