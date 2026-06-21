import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { axe } from "jest-axe";
import { expect, it } from "vitest";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import { InterestedPartiesRegisterPage } from "./InterestedPartiesRegisterPage";

// Flip the register head's state + the SERVER-computed caps (the gating source — the page gates on the
// server can_manage/can_release, NOT a /me/permissions probe; clause 4.2 is org-level).
function registerState(state: string, caps?: { can_release?: boolean; can_manage?: boolean }) {
  server.use(
    http.get("/api/v1/interested-parties/register", () =>
      HttpResponse.json({
        exists: true,
        register_doc_id: "ee000000-0000-0000-0000-0000000000bb",
        identifier: "IPR-001",
        state,
        current_effective_version_id: "ve000001-0001-0001-0001-000000000001",
        has_governing: true,
        can_release: caps?.can_release ?? false,
        can_manage: caps?.can_manage ?? false,
      }),
    ),
  );
}

const ROUTE = "/interested-parties";

it("renders the party-type board, scorecard, and a table row per party", async () => {
  const { container } = renderWithProviders(<InterestedPartiesRegisterPage />, { route: ROUTE });
  await waitFor(() =>
    expect(
      screen.getByRole("region", { name: "Interested parties by type — 6 parties" }),
    ).toBeInTheDocument(),
  );
  // the board buckets by party type
  expect(screen.getByRole("group", { name: "Customers, 3 parties" })).toBeInTheDocument();
  // the scorecard rolls up client-side from the live rows (5 active, 2 high-influence, 2 never reviewed)
  expect(screen.getByText("5 of 6 active")).toBeInTheDocument();
  expect(screen.getByText("2 high")).toBeInTheDocument();
  expect(screen.getByText("2 never reviewed")).toBeInTheDocument();
  // the table lists each party by name
  const table = screen.getByRole("table");
  expect(within(table).getByText("Acme Manufacturing")).toBeInTheDocument();
  expect(await axe(container)).toHaveNoViolations();
});

it("shows the read-only banner and hides New when the register is Effective", async () => {
  registerState("Effective", { can_manage: true }); // even WITH manage, Effective hides New (not editable)
  renderWithProviders(<InterestedPartiesRegisterPage />, { route: ROUTE });
  await waitFor(() =>
    expect(screen.getByText(/this register is effective \(read-only\)/i)).toBeInTheDocument(),
  );
  expect(screen.queryByRole("button", { name: "New party" })).not.toBeInTheDocument();
});

it("shows New (and opens the create modal) when editable + can_manage", async () => {
  registerState("UnderRevision", { can_manage: true });
  const user = userEvent.setup();
  renderWithProviders(<InterestedPartiesRegisterPage />, { route: ROUTE });
  await user.click(await screen.findByRole("button", { name: "New party" }));
  expect(await screen.findByRole("dialog")).toHaveTextContent(/new interested party/i);
});

it("hides New when editable but the caller lacks can_manage", async () => {
  registerState("UnderRevision", { can_manage: false });
  renderWithProviders(<InterestedPartiesRegisterPage />, { route: ROUTE });
  await waitFor(() =>
    expect(screen.getByRole("region", { name: /Interested parties by type/ })).toBeInTheDocument(),
  );
  expect(screen.queryByRole("button", { name: "New party" })).not.toBeInTheDocument();
});

it("creates a party from the modal (the create flow)", async () => {
  registerState("UnderRevision", { can_manage: true });
  const user = userEvent.setup();
  renderWithProviders(<InterestedPartiesRegisterPage />, { route: ROUTE });
  await user.click(await screen.findByRole("button", { name: "New party" }));
  await user.type(await screen.findByLabelText(/Party name/), "Key supplier consortium");
  await user.type(screen.getByLabelText(/Needs/), "Stable orders and clear specs");
  await user.click(screen.getByRole("button", { name: "Create party" }));
  // the modal closes on success (the create POST resolved)
  await waitFor(() => expect(screen.queryByText("New interested party")).not.toBeInTheDocument());
});

it("the influence filter narrows the table (the board stays whole)", async () => {
  const user = userEvent.setup();
  renderWithProviders(<InterestedPartiesRegisterPage />, { route: ROUTE });
  await waitFor(() => expect(screen.getByRole("table")).toBeInTheDocument());
  await user.click(screen.getByRole("radio", { name: "High" }));
  const table = screen.getByRole("table");
  expect(within(table).getByText("Acme Manufacturing")).toBeInTheDocument(); // high
  expect(within(table).queryByText("Beta Retail Group")).not.toBeInTheDocument(); // medium filtered out…
  // …but the board still shows the whole register (the medium chip remains)
  expect(screen.getByText("Beta Retail Group")).toBeInTheDocument();
});

it("the party-type filter (the Select) narrows the table", async () => {
  const user = userEvent.setup();
  renderWithProviders(<InterestedPartiesRegisterPage />, { route: ROUTE });
  await waitFor(() => expect(screen.getByRole("table")).toBeInTheDocument());
  await user.click(screen.getByLabelText("Filter by party type"));
  await user.click(await screen.findByRole("option", { name: "Regulator" }));
  const table = screen.getByRole("table");
  expect(within(table).getByText("National accreditation body")).toBeInTheDocument();
  expect(within(table).queryByText("Acme Manufacturing")).not.toBeInTheDocument();
});

it("the status filter narrows to closed parties", async () => {
  const user = userEvent.setup();
  renderWithProviders(<InterestedPartiesRegisterPage />, { route: ROUTE });
  await waitFor(() => expect(screen.getByRole("table")).toBeInTheDocument());
  await user.click(screen.getByRole("radio", { name: "Closed" }));
  const table = screen.getByRole("table");
  expect(within(table).getByText("Former distributor (legacy)")).toBeInTheDocument();
  expect(within(table).queryByText("Acme Manufacturing")).not.toBeInTheDocument();
});

it("a ?party= deep-link opens the detail drawer", async () => {
  renderWithProviders(<InterestedPartiesRegisterPage />, {
    route: "/interested-parties?party=ee000002-0002-0002-0002-000000000002",
  });
  const dialog = await screen.findByRole("dialog");
  expect(await within(dialog).findByText("Beta Retail Group")).toBeInTheDocument();
});

it("opens the drawer from a table row anchor", async () => {
  const user = userEvent.setup();
  renderWithProviders(<InterestedPartiesRegisterPage />, { route: ROUTE });
  await waitFor(() => expect(screen.getByRole("table")).toBeInTheDocument());
  await user.click(within(screen.getByRole("table")).getByText("Acme Manufacturing"));
  const dialog = await screen.findByRole("dialog");
  expect(await within(dialog).findByText("Acme Manufacturing")).toBeInTheDocument();
});

it("a filter change does not close a locally-opened drawer (the ?party-keyed sync effect)", async () => {
  const user = userEvent.setup();
  renderWithProviders(<InterestedPartiesRegisterPage />, { route: ROUTE });
  await waitFor(() => expect(screen.getByRole("table")).toBeInTheDocument());
  await user.click(within(screen.getByRole("table")).getByText("Beta Retail Group")); // local open (no URL change)
  expect(await screen.findByRole("dialog")).toBeInTheDocument();
  // change the influence filter (writes ?influence= to the URL) — the drawer must STAY open because the
  // sync effect keys on ?party= alone, not the whole search-params (the S-context-fe Codex P3 lesson).
  await user.click(screen.getByRole("radio", { name: "High" }));
  expect(screen.getByRole("dialog")).toBeInTheDocument();
});

it("shows an empty state when there are no interested parties", async () => {
  server.use(http.get("/api/v1/interested-parties", () => HttpResponse.json({ data: [] })));
  renderWithProviders(<InterestedPartiesRegisterPage />, { route: ROUTE });
  await waitFor(() => expect(screen.getByText("No interested parties yet")).toBeInTheDocument());
});

// ---- the register-steward lifecycle console ----

it("hides the steward console for a non-steward (no can_manage / can_release)", async () => {
  renderWithProviders(<InterestedPartiesRegisterPage />, { route: ROUTE }); // default caps = false
  await waitFor(() => expect(screen.getByRole("table")).toBeInTheDocument());
  expect(screen.queryByText("Register lifecycle")).not.toBeInTheDocument();
});

it("shows Start revision on an Effective register (can_manage)", async () => {
  registerState("Effective", { can_manage: true });
  renderWithProviders(<InterestedPartiesRegisterPage />, { route: ROUTE });
  await waitFor(() => expect(screen.getByText("Register lifecycle")).toBeInTheDocument());
  expect(screen.getByRole("button", { name: "Start revision" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Publish revision" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Release" })).not.toBeInTheDocument();
});

it("publishes a register revision from the console on an editable head", async () => {
  registerState("UnderRevision", { can_manage: true });
  const user = userEvent.setup();
  renderWithProviders(<InterestedPartiesRegisterPage />, { route: ROUTE });
  await user.click(await screen.findByRole("button", { name: "Publish revision" }));
  expect(await screen.findByText("Publish register revision")).toBeInTheDocument();
  await user.type(screen.getByLabelText("Change reason"), "Annual review");
  await user.click(screen.getByRole("button", { name: "Publish" }));
  await waitFor(() =>
    expect(screen.queryByText("Publish register revision")).not.toBeInTheDocument(),
  );
});

it("lets Publish proceed (not client-disabled) and surfaces the server's empty-register 409", async () => {
  // a manage-without-read steward sees 0 filtered parties for a non-empty register, so the FE must NOT
  // gate Publish on the client count — the server's 409 is the source of truth (surfaces in the modal).
  registerState("Draft", { can_manage: true });
  server.use(
    http.get("/api/v1/interested-parties", () => HttpResponse.json({ data: [] })),
    http.post("/api/v1/interested-parties/register/publish", () =>
      HttpResponse.json(
        { code: "register_empty", title: "Interested-parties register has no parties to publish." },
        { status: 409 },
      ),
    ),
  );
  const user = userEvent.setup();
  renderWithProviders(<InterestedPartiesRegisterPage />, { route: ROUTE });
  await waitFor(() => expect(screen.getByText("Register lifecycle")).toBeInTheDocument());
  const publishBtn = screen.getByRole("button", { name: "Publish revision" });
  expect(publishBtn).toBeEnabled();
  await user.click(publishBtn);
  expect(await screen.findByText("Publish register revision")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Publish" }));
  await waitFor(() =>
    expect(
      screen.getByText("Interested-parties register has no parties to publish."),
    ).toBeInTheDocument(),
  );
  expect(screen.getByText("Publish register revision")).toBeInTheDocument();
});

it("gates Release on the server can_release boolean, not a single-axis FE probe", async () => {
  // release authz is multi-axis (artifact + folder + level + lifecycle_state + SoD-2) — the page trusts
  // the server-computed can_release. Approved WITHOUT can_release must NOT surface Release…
  registerState("Approved", { can_release: false });
  const { unmount } = renderWithProviders(<InterestedPartiesRegisterPage />, { route: ROUTE });
  await waitFor(() => expect(screen.getByRole("table")).toBeInTheDocument());
  expect(screen.queryByRole("button", { name: "Release" })).not.toBeInTheDocument();
  unmount();
  // …while can_release:true surfaces it.
  registerState("Approved", { can_release: true });
  renderWithProviders(<InterestedPartiesRegisterPage />, { route: ROUTE });
  await waitFor(() => expect(screen.getByRole("button", { name: "Release" })).toBeInTheDocument());
});

it("shows Release on an Approved register (can_release) and runs the confirm", async () => {
  registerState("Approved", { can_release: true });
  const user = userEvent.setup();
  renderWithProviders(<InterestedPartiesRegisterPage />, { route: ROUTE });
  await user.click(await screen.findByRole("button", { name: "Release" }));
  const dialog = await screen.findByRole("dialog");
  expect(
    within(dialog).getByText(/promotes the approved version to effective/i),
  ).toBeInTheDocument();
  await user.click(within(dialog).getByRole("button", { name: "Release" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
});

it("the Release confirm stays open and surfaces the SoD-2 reason on a 409", async () => {
  registerState("Approved", { can_release: true });
  server.use(
    http.post("/api/v1/interested-parties/register/release", () =>
      HttpResponse.json(
        { code: "sod_violation", title: "You can't release a revision you authored." },
        { status: 409 },
      ),
    ),
  );
  const user = userEvent.setup();
  renderWithProviders(<InterestedPartiesRegisterPage />, { route: ROUTE });
  await user.click(await screen.findByRole("button", { name: "Release" }));
  const dialog = await screen.findByRole("dialog");
  await user.click(within(dialog).getByRole("button", { name: "Release" }));
  await waitFor(() =>
    expect(
      within(dialog).getByText("You can't release a revision you authored."),
    ).toBeInTheDocument(),
  );
  expect(screen.getByRole("dialog")).toBeInTheDocument();
});

it("points the steward to Tasks while a revision is in review", async () => {
  registerState("InReview", { can_manage: true });
  renderWithProviders(<InterestedPartiesRegisterPage />, { route: ROUTE });
  await waitFor(() => expect(screen.getByText(/an approver decides in/i)).toBeInTheDocument());
  expect(screen.getByRole("link", { name: "Tasks" })).toHaveAttribute("href", "/tasks");
});
