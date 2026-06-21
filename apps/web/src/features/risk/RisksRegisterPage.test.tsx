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

// Flip the register head's lifecycle state (the steward-console gating source).
function registerState(state: string) {
  server.use(
    http.get("/api/v1/risks/register", () =>
      HttpResponse.json({
        exists: true,
        register_doc_id: "d0c00000-0000-0000-0000-0000000000aa",
        identifier: "RSK-001",
        state,
        current_effective_version_id: "ve000001-0001-0001-0001-000000000001",
        has_governing: true,
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

it("a filter change does not close a locally-opened drawer (the ?risk-keyed sync effect)", async () => {
  const user = userEvent.setup();
  renderWithProviders(<RisksRegisterPage />, { route: "/risks" });
  await waitFor(() =>
    expect(screen.getByText("Untrained operators on the new line")).toBeInTheDocument(),
  );
  await user.click(screen.getByText("Untrained operators on the new line")); // local open (no URL change)
  expect(await screen.findByRole("dialog")).toBeInTheDocument();
  // change the band filter (writes ?band= to the URL) — the drawer must stay open because the sync
  // effect keys on ?risk= alone, not the whole search-params (Codex P3 fix didn't over-close).
  await user.click(screen.getByRole("radio", { name: "Medium" }));
  expect(screen.getByRole("dialog")).toBeInTheDocument();
});

it("shows an empty state when there are no risks", async () => {
  server.use(http.get("/api/v1/risks", () => HttpResponse.json({ data: [] })));
  renderWithProviders(<RisksRegisterPage />, { route: "/risks" });
  await waitFor(() =>
    expect(screen.getByText(/no risks or opportunities yet/i)).toBeInTheDocument(),
  );
});

// ---- S-risk-5 register-steward lifecycle console ----

it("hides the steward console for a non-steward (no register.manage / document.release)", async () => {
  // default /me/permissions = empty → no steward affordance
  renderWithProviders(<RisksRegisterPage />, { route: "/risks" });
  await waitFor(() =>
    expect(screen.getByText("Supplier single point of failure")).toBeInTheDocument(),
  );
  expect(screen.queryByText("Register lifecycle")).not.toBeInTheDocument();
});

it("the console shows Start revision on an Effective register (register.manage @ SYSTEM)", async () => {
  grant("register.manage"); // default fixture state = Effective
  renderWithProviders(<RisksRegisterPage />, { route: "/risks" });
  await waitFor(() => expect(screen.getByText("Register lifecycle")).toBeInTheDocument());
  expect(screen.getByRole("button", { name: "Start revision" })).toBeInTheDocument();
  // Effective is not editable + no document.release → no Publish / Release affordance (quiet absence)
  expect(screen.queryByRole("button", { name: "Publish revision" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Release" })).not.toBeInTheDocument();
});

it("the console is hidden for a PROCESS-only register.manage holder (org head is SYSTEM-only)", async () => {
  registerState("UnderRevision"); // editable head, but…
  server.use(
    http.get("/api/v1/me/permissions", ({ request }) => {
      const level = new URL(request.url).searchParams.get("scope_level");
      return HttpResponse.json({
        scope: { level: level ?? "SYSTEM", selector: null },
        permissions:
          level === "PROCESS" ? [{ key: "register.manage", effect: "ALLOW", source: "test" }] : [],
      });
    }),
  );
  renderWithProviders(<RisksRegisterPage />, { route: "/risks" });
  await waitFor(() =>
    expect(screen.getByText("Supplier single point of failure")).toBeInTheDocument(),
  );
  // a PROCESS-only holder CAN create rows (the New button, via the first-readable-process probe)…
  expect(screen.getByRole("button", { name: "New risk" })).toBeInTheDocument();
  // …but CANNOT steward the org head — the console is SYSTEM-gated, so it's hidden.
  expect(screen.queryByText("Register lifecycle")).not.toBeInTheDocument();
});

it("publishes a register revision from the console on an editable head", async () => {
  registerState("UnderRevision");
  grant("register.manage");
  const user = userEvent.setup();
  renderWithProviders(<RisksRegisterPage />, { route: "/risks" });
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Publish revision" })).toBeInTheDocument(),
  );
  await user.click(screen.getByRole("button", { name: "Publish revision" }));
  // the publish modal opens with an optional change-reason field
  expect(await screen.findByText("Publish register revision")).toBeInTheDocument();
  await user.type(screen.getByLabelText("Change reason"), "Q3 reassessment");
  // exact name → the modal's submit, not the "Publish revision" opener
  await user.click(screen.getByRole("button", { name: "Publish" }));
  // the modal closes on success
  await waitFor(() =>
    expect(screen.queryByText("Publish register revision")).not.toBeInTheDocument(),
  );
});

it("disables Publish until the register has at least one row", async () => {
  registerState("Draft");
  grant("register.manage");
  server.use(http.get("/api/v1/risks", () => HttpResponse.json({ data: [] })));
  renderWithProviders(<RisksRegisterPage />, { route: "/risks" });
  await waitFor(() => expect(screen.getByText("Register lifecycle")).toBeInTheDocument());
  expect(screen.getByRole("button", { name: "Publish revision" })).toBeDisabled();
  expect(screen.getByText("Add a risk before publishing.")).toBeInTheDocument();
});

it("shows Release on an Approved register (document.release) and runs the confirm", async () => {
  registerState("Approved");
  grant("document.release"); // the cutover key (SYSTEM-override-only in v1)
  const user = userEvent.setup();
  renderWithProviders(<RisksRegisterPage />, { route: "/risks" });
  await waitFor(() => expect(screen.getByRole("button", { name: "Release" })).toBeInTheDocument());
  await user.click(screen.getByRole("button", { name: "Release" }));
  const dialog = await screen.findByRole("dialog");
  expect(
    within(dialog).getByText(/promotes the approved version to effective/i),
  ).toBeInTheDocument();
  await user.click(within(dialog).getByRole("button", { name: "Release" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
});

it("the Release confirm stays open and surfaces the SoD-2 reason on a 409", async () => {
  registerState("Approved");
  grant("document.release");
  server.use(
    http.post("/api/v1/risks/register/release", () =>
      HttpResponse.json(
        { code: "sod_violation", title: "You can't release a revision you authored." },
        { status: 409 },
      ),
    ),
  );
  const user = userEvent.setup();
  renderWithProviders(<RisksRegisterPage />, { route: "/risks" });
  await waitFor(() => expect(screen.getByRole("button", { name: "Release" })).toBeInTheDocument());
  await user.click(screen.getByRole("button", { name: "Release" }));
  const dialog = await screen.findByRole("dialog");
  await user.click(within(dialog).getByRole("button", { name: "Release" }));
  // the server reason lands calmly in-dialog and the confirm STAYS OPEN (ConfirmDestructive trap)
  await waitFor(() =>
    expect(
      within(dialog).getByText("You can't release a revision you authored."),
    ).toBeInTheDocument(),
  );
  expect(screen.getByRole("dialog")).toBeInTheDocument();
});

it("a document.release-only steward sees the card but no actionable button on Effective", async () => {
  // the OR-gate's second leg: canRelease (document.release) opens the console, but on an Effective
  // head with no register.manage there's nothing to release/publish/start → quiet absence, no
  // instruction to an action the holder can't take.
  grant("document.release"); // default fixture state = Effective
  renderWithProviders(<RisksRegisterPage />, { route: "/risks" });
  await waitFor(() => expect(screen.getByText("Register lifecycle")).toBeInTheDocument());
  expect(screen.queryByRole("button", { name: "Release" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Start revision" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Publish revision" })).not.toBeInTheDocument();
});

it("points the steward to Tasks while a revision is in review", async () => {
  registerState("InReview");
  grant("register.manage");
  renderWithProviders(<RisksRegisterPage />, { route: "/risks" });
  await waitFor(() => expect(screen.getByText(/an approver decides in/i)).toBeInTheDocument());
  expect(screen.getByRole("link", { name: "Tasks" })).toHaveAttribute("href", "/tasks");
});
