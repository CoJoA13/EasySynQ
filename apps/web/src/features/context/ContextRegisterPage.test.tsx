import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { axe } from "jest-axe";
import { expect, it } from "vitest";
import { renderWithProviders } from "../../test/render";
import { server } from "../../test/msw/server";
import { ContextRegisterPage } from "./ContextRegisterPage";

// Flip the register head's state + the SERVER-computed caps (the gating source — context gates on the
// server can_manage/can_release, NOT a /me/permissions probe; clause 4.1 is org-level).
function registerState(state: string, caps?: { can_release?: boolean; can_manage?: boolean }) {
  server.use(
    http.get("/api/v1/context/register", () =>
      HttpResponse.json({
        exists: true,
        register_doc_id: "c0c00000-0000-0000-0000-0000000000bb",
        identifier: "CTX-001",
        state,
        current_effective_version_id: "vc000001-0001-0001-0001-000000000001",
        has_governing: true,
        can_release: caps?.can_release ?? false,
        can_manage: caps?.can_manage ?? false,
      }),
    ),
  );
}

it("renders the SWOT board, scorecard, and a table row per issue", async () => {
  const { container } = renderWithProviders(<ContextRegisterPage />, { route: "/context" });
  await waitFor(() =>
    expect(
      screen.getByRole("region", { name: "SWOT analysis of 5 context issues" }),
    ).toBeInTheDocument(),
  );
  // the board buckets by category
  expect(screen.getByRole("group", { name: "Strengths, 1 issue" })).toBeInTheDocument();
  // the scorecard rolls up client-side from the live rows (4 active, 3 internal / 2 external, 2 never)
  expect(screen.getByText("4 of 5 active")).toBeInTheDocument();
  expect(screen.getByText("3 internal")).toBeInTheDocument();
  expect(screen.getByText("2 never reviewed")).toBeInTheDocument();
  // the table lists each issue with its classification badge
  const table = screen.getByRole("table");
  expect(within(table).getByText("Skilled and certified QA team")).toBeInTheDocument();
  expect(await axe(container)).toHaveNoViolations();
});

it("shows the read-only banner and hides New when the register is Effective", async () => {
  registerState("Effective", { can_manage: true }); // even WITH manage, Effective hides New (not editable)
  renderWithProviders(<ContextRegisterPage />, { route: "/context" });
  await waitFor(() =>
    expect(screen.getByText(/this register is effective \(read-only\)/i)).toBeInTheDocument(),
  );
  expect(screen.queryByRole("button", { name: "New issue" })).not.toBeInTheDocument();
});

it("shows New (and opens the create modal) when editable + can_manage", async () => {
  registerState("UnderRevision", { can_manage: true });
  const user = userEvent.setup();
  renderWithProviders(<ContextRegisterPage />, { route: "/context" });
  await user.click(await screen.findByRole("button", { name: "New issue" }));
  expect(await screen.findByRole("dialog")).toHaveTextContent(/new context issue/i);
});

it("hides New when editable but the caller lacks can_manage", async () => {
  registerState("UnderRevision", { can_manage: false });
  renderWithProviders(<ContextRegisterPage />, { route: "/context" });
  await waitFor(() =>
    expect(screen.getByRole("region", { name: /SWOT analysis/ })).toBeInTheDocument(),
  );
  expect(screen.queryByRole("button", { name: "New issue" })).not.toBeInTheDocument();
});

it("creates an issue from the modal (the create flow)", async () => {
  registerState("UnderRevision", { can_manage: true });
  const user = userEvent.setup();
  renderWithProviders(<ContextRegisterPage />, { route: "/context" });
  await user.click(await screen.findByRole("button", { name: "New issue" }));
  await user.type(await screen.findByLabelText(/Description/), "New external regulatory change");
  await user.click(screen.getByRole("button", { name: "Create issue" }));
  // the modal closes on success (the create POST resolved)
  await waitFor(() => expect(screen.queryByText("New context issue")).not.toBeInTheDocument());
});

it("the classification filter narrows the table (the SWOT board stays whole)", async () => {
  const user = userEvent.setup();
  renderWithProviders(<ContextRegisterPage />, { route: "/context" });
  await waitFor(() => expect(screen.getByRole("table")).toBeInTheDocument());
  await user.click(screen.getByRole("radio", { name: "Internal" }));
  const table = screen.getByRole("table");
  expect(within(table).getByText("Skilled and certified QA team")).toBeInTheDocument(); // internal
  expect(
    within(table).queryByText("Growing demand for ISO-certified suppliers"),
  ).not.toBeInTheDocument(); // external row filtered out of the table…
  // …but the board still shows the whole register (the external opportunity chip remains)
  expect(screen.getByText("Growing demand for ISO-certified suppliers")).toBeInTheDocument();
});

it("the category filter narrows to uncategorized issues", async () => {
  const user = userEvent.setup();
  renderWithProviders(<ContextRegisterPage />, { route: "/context" });
  await waitFor(() => expect(screen.getByRole("table")).toBeInTheDocument());
  await user.click(screen.getByRole("radio", { name: "Uncategorized" }));
  const table = screen.getByRole("table");
  expect(within(table).getByText("Pending reorganisation of the QA function")).toBeInTheDocument();
  expect(within(table).queryByText("Skilled and certified QA team")).not.toBeInTheDocument();
});

it("the status filter narrows to closed issues", async () => {
  const user = userEvent.setup();
  renderWithProviders(<ContextRegisterPage />, { route: "/context" });
  await waitFor(() => expect(screen.getByRole("table")).toBeInTheDocument());
  await user.click(screen.getByRole("radio", { name: "Closed" }));
  const table = screen.getByRole("table");
  expect(within(table).getByText("Pending reorganisation of the QA function")).toBeInTheDocument();
  expect(within(table).queryByText("Skilled and certified QA team")).not.toBeInTheDocument();
});

it("a ?issue= deep-link opens the detail drawer", async () => {
  renderWithProviders(<ContextRegisterPage />, {
    route: "/context?issue=cc000002-0002-0002-0002-000000000002",
  });
  const dialog = await screen.findByRole("dialog");
  expect(
    await within(dialog).findByText("Legacy on-disk mirror is hard to maintain"),
  ).toBeInTheDocument();
});

it("opens the drawer from a table row anchor", async () => {
  const user = userEvent.setup();
  renderWithProviders(<ContextRegisterPage />, { route: "/context" });
  await waitFor(() => expect(screen.getByRole("table")).toBeInTheDocument());
  await user.click(within(screen.getByRole("table")).getByText("Skilled and certified QA team"));
  const dialog = await screen.findByRole("dialog");
  expect(await within(dialog).findByText("Skilled and certified QA team")).toBeInTheDocument();
});

it("a filter change does not close a locally-opened drawer (the ?issue-keyed sync effect)", async () => {
  const user = userEvent.setup();
  renderWithProviders(<ContextRegisterPage />, { route: "/context" });
  await waitFor(() => expect(screen.getByRole("table")).toBeInTheDocument());
  await user.click(
    within(screen.getByRole("table")).getByText("Legacy on-disk mirror is hard to maintain"),
  ); // local open (no URL change)
  expect(await screen.findByRole("dialog")).toBeInTheDocument();
  // change the classification filter (writes ?classification= to the URL) — the drawer must STAY open
  // because the sync effect keys on ?issue= alone, not the whole search-params (Codex P3).
  await user.click(screen.getByRole("radio", { name: "External" }));
  expect(screen.getByRole("dialog")).toBeInTheDocument();
});

it("shows an empty state when there are no context issues", async () => {
  server.use(http.get("/api/v1/context", () => HttpResponse.json({ data: [] })));
  renderWithProviders(<ContextRegisterPage />, { route: "/context" });
  await waitFor(() => expect(screen.getByText("No context issues yet")).toBeInTheDocument());
});

// ---- the register-steward lifecycle console ----

it("hides the steward console for a non-steward (no can_manage / can_release)", async () => {
  renderWithProviders(<ContextRegisterPage />, { route: "/context" }); // default caps = false
  await waitFor(() => expect(screen.getByRole("table")).toBeInTheDocument());
  expect(screen.queryByText("Register lifecycle")).not.toBeInTheDocument();
});

it("shows Start revision on an Effective register (can_manage)", async () => {
  registerState("Effective", { can_manage: true });
  renderWithProviders(<ContextRegisterPage />, { route: "/context" });
  await waitFor(() => expect(screen.getByText("Register lifecycle")).toBeInTheDocument());
  expect(screen.getByRole("button", { name: "Start revision" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Publish revision" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Release" })).not.toBeInTheDocument();
});

it("publishes a register revision from the console on an editable head", async () => {
  registerState("UnderRevision", { can_manage: true });
  const user = userEvent.setup();
  renderWithProviders(<ContextRegisterPage />, { route: "/context" });
  await user.click(await screen.findByRole("button", { name: "Publish revision" }));
  expect(await screen.findByText("Publish register revision")).toBeInTheDocument();
  await user.type(screen.getByLabelText("Change reason"), "Annual review");
  await user.click(screen.getByRole("button", { name: "Publish" }));
  await waitFor(() =>
    expect(screen.queryByText("Publish register revision")).not.toBeInTheDocument(),
  );
});

it("lets Publish proceed (not client-disabled) and surfaces the server's empty-register 409", async () => {
  // a manage-without-read steward sees 0 filtered issues for a non-empty register, so the FE must NOT
  // gate Publish on the client count — the server's 409 is the source of truth (surfaces in the modal).
  registerState("Draft", { can_manage: true });
  server.use(
    http.get("/api/v1/context", () => HttpResponse.json({ data: [] })),
    http.post("/api/v1/context/register/publish", () =>
      HttpResponse.json(
        { code: "register_empty", title: "Context register has no issues to publish." },
        { status: 409 },
      ),
    ),
  );
  const user = userEvent.setup();
  renderWithProviders(<ContextRegisterPage />, { route: "/context" });
  await waitFor(() => expect(screen.getByText("Register lifecycle")).toBeInTheDocument());
  const publishBtn = screen.getByRole("button", { name: "Publish revision" });
  expect(publishBtn).toBeEnabled();
  await user.click(publishBtn);
  expect(await screen.findByText("Publish register revision")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Publish" }));
  await waitFor(() =>
    expect(screen.getByText("Context register has no issues to publish.")).toBeInTheDocument(),
  );
  expect(screen.getByText("Publish register revision")).toBeInTheDocument();
});

it("gates Release on the server can_release boolean, not a single-axis FE probe", async () => {
  // release authz is multi-axis (artifact + folder + level + lifecycle_state + SoD-2) — the page trusts
  // the server-computed can_release. Approved WITHOUT can_release must NOT surface Release…
  registerState("Approved", { can_release: false });
  const { unmount } = renderWithProviders(<ContextRegisterPage />, { route: "/context" });
  await waitFor(() => expect(screen.getByRole("table")).toBeInTheDocument());
  expect(screen.queryByRole("button", { name: "Release" })).not.toBeInTheDocument();
  unmount();
  // …while can_release:true surfaces it.
  registerState("Approved", { can_release: true });
  renderWithProviders(<ContextRegisterPage />, { route: "/context" });
  await waitFor(() => expect(screen.getByRole("button", { name: "Release" })).toBeInTheDocument());
});

it("shows Release on an Approved register (can_release) and runs the confirm", async () => {
  registerState("Approved", { can_release: true });
  const user = userEvent.setup();
  renderWithProviders(<ContextRegisterPage />, { route: "/context" });
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
    http.post("/api/v1/context/register/release", () =>
      HttpResponse.json(
        { code: "sod_violation", title: "You can't release a revision you authored." },
        { status: 409 },
      ),
    ),
  );
  const user = userEvent.setup();
  renderWithProviders(<ContextRegisterPage />, { route: "/context" });
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
  renderWithProviders(<ContextRegisterPage />, { route: "/context" });
  await waitFor(() => expect(screen.getByText(/an approver decides in/i)).toBeInTheDocument());
  expect(screen.getByRole("link", { name: "Tasks" })).toHaveAttribute("href", "/tasks");
});
