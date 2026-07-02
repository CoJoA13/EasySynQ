import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { expect, it, test } from "vitest";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { LeftRail } from "./LeftRail";

// Grant every gated nav key so the full PDCA grouping is visible.
function grantAll() {
  server.use(
    http.get("/api/v1/me/permissions", () =>
      HttpResponse.json({
        scope: { level: "SYSTEM", selector: null },
        permissions: [
          "objective.read",
          "import.review",
          "report.compliance_checklist.read",
          "mgmtReview.read",
          "drift.read",
          "improvement.read",
          "changeRequest.read",
        ].map((key) => ({ key, effect: "ALLOW", source: "test" })),
      }),
    ),
  );
}

test("LeftRail shows Home + the four PDCA phase headings (with clause ranges)", async () => {
  renderWithProviders(<LeftRail />, { route: "/library" });
  expect(screen.getByRole("link", { name: "Home" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Library" })).toBeInTheDocument();
  await waitFor(() => expect(screen.getByText(/PLAN ·/)).toBeInTheDocument());
  expect(screen.getByText(/DO ·/)).toBeInTheDocument();
  expect(screen.getByText(/CHECK ·/)).toBeInTheDocument();
  expect(screen.getByText(/ACT ·/)).toBeInTheDocument();
});

test("Library + Review and approve sit under the DO section", () => {
  renderWithProviders(<LeftRail />, { route: "/library" });
  const doSection = screen.getByRole("group", { name: "DO section" });
  expect(within(doSection).getByRole("link", { name: "Library" })).toHaveAttribute(
    "href",
    "/library",
  );
  expect(within(doSection).getByRole("link", { name: "Review and approve" })).toHaveAttribute(
    "href",
    "/tasks",
  );
});

test("Change requests (DCR) sits under the ACT section, beside CAPA + Improvement", async () => {
  grantAll();
  renderWithProviders(<LeftRail />, { route: "/" });
  // wait for the gated DCR link (perms resolve async), then assert its placement
  const dcr = await screen.findByRole("link", { name: "Change requests" });
  expect(dcr).toHaveAttribute("href", "/dcrs");
  const act = screen.getByRole("group", { name: "ACT section" });
  expect(act).toContainElement(dcr);
  expect(within(act).getByRole("link", { name: "Nonconformity and CAPA" })).toBeInTheDocument();
  expect(within(act).getByRole("link", { name: "Improvement" })).toBeInTheDocument();
});

test("Objectives sits under the PLAN section (gated on objective.read)", async () => {
  grantAll();
  renderWithProviders(<LeftRail />, { route: "/" });
  const objectives = await screen.findByRole("link", { name: "Objectives" });
  expect(objectives).toHaveAttribute("href", "/objectives");
  const plan = screen.getByRole("group", { name: "PLAN section" });
  expect(plan).toContainElement(objectives);
});

test("each phase's clause-filter links nest under a collapsed per-phase disclosure", async () => {
  renderWithProviders(<LeftRail />, { route: "/library" });
  const plan = await screen.findByRole("group", { name: "PLAN section" });
  // The clause-browse links live behind one collapsed "Clauses 4–6" disclosure per phase (they are
  // Library filters, not registers). Collapsed → no clause link is exposed to the a11y tree, and the
  // toggle MUST announce its state (Mantine emits only data-expanded — a styling hook AT can't hear).
  const toggle = await within(plan).findByRole("button", { name: /Clauses 4–6/ });
  expect(toggle).toHaveAttribute("aria-expanded", "false");
  expect(
    within(plan)
      .queryAllByRole("link")
      .some((a) => a.getAttribute("href")?.startsWith("/library?clause=")),
  ).toBe(false);
  await userEvent.click(toggle);
  expect(toggle).toHaveAttribute("aria-expanded", "true");
  expect(
    within(plan)
      .getAllByRole("link")
      .some((a) => a.getAttribute("href")?.startsWith("/library?clause=")),
  ).toBe(true);
});

test("a deep link to a clause filter auto-opens the owning phase's disclosure", async () => {
  // Landing directly on /library?clause=4.1 (CompliancePage/search links do this) must not hide the
  // applied filter's own rail link behind a collapsed disclosure.
  renderWithProviders(<LeftRail />, { route: "/library?clause=4.1" });
  const plan = await screen.findByRole("group", { name: "PLAN section" });
  const toggle = await within(plan).findByRole("button", { name: /Clauses 4–6/ });
  expect(toggle).toHaveAttribute("aria-expanded", "true");
  expect(
    within(plan)
      .getAllByRole("link")
      .some((a) => a.getAttribute("href")?.startsWith("/library?clause=4")),
  ).toBe(true);
});

test("the Nonconformity and CAPA entry is always shown (discoverable; page handles 403)", async () => {
  renderWithProviders(<LeftRail />, { route: "/" });
  expect(await screen.findByText("Nonconformity and CAPA")).toBeInTheDocument();
});

test("hides the Compliance entry when the caller lacks report.compliance_checklist.read", async () => {
  renderWithProviders(<LeftRail />, { route: "/" });
  await screen.findByText("Library");
  expect(screen.queryByText("Compliance")).not.toBeInTheDocument();
});

test("shows the gated Compliance entry when the caller holds the key", async () => {
  server.use(
    http.get("/api/v1/me/permissions", () =>
      HttpResponse.json({
        scope: { level: "SYSTEM", selector: null },
        permissions: [{ key: "report.compliance_checklist.read", effect: "ALLOW", source: "role" }],
      }),
    ),
  );
  renderWithProviders(<LeftRail />, { route: "/" });
  expect(await screen.findByText("Compliance")).toBeInTheDocument();
});

test("hides the Import entry when the caller lacks import.review", async () => {
  // default MSW /me/permissions returns no key → the admin-only Import entry is hidden
  renderWithProviders(<LeftRail />, { route: "/" });
  await screen.findByText("Library");
  expect(screen.queryByText("Import")).not.toBeInTheDocument();
});

test("shows the gated Import entry when the caller holds import.review", async () => {
  server.use(
    http.get("/api/v1/me/permissions", () =>
      HttpResponse.json({
        scope: { level: "SYSTEM", selector: null },
        permissions: [{ key: "import.review", effect: "ALLOW", source: "role" }],
      }),
    ),
  );
  renderWithProviders(<LeftRail />, { route: "/ingestion" });
  const link = await screen.findByRole("link", { name: "Import" });
  expect(link).toHaveAttribute("href", "/ingestion");
});

test("Internal audit entry is unconditional (the CAPA precedent — calm-403 lives on the page)", async () => {
  renderWithProviders(<LeftRail />);
  expect(await screen.findByRole("link", { name: "Internal audit" })).toHaveAttribute(
    "href",
    "/audits",
  );
});

it("shows the Objectives entry only with objective.read", async () => {
  renderWithProviders(<LeftRail />);
  // default permissions handler grants nothing → no entry
  await waitFor(() => expect(screen.getByText("Home")).toBeInTheDocument());
  expect(screen.queryByText("Objectives")).not.toBeInTheDocument();

  server.use(
    http.get("/api/v1/me/permissions", () =>
      HttpResponse.json({
        scope: { level: "SYSTEM", selector: null },
        permissions: [{ key: "objective.read", effect: "ALLOW", source: "test" }],
      }),
    ),
  );
  renderWithProviders(<LeftRail />);
  await waitFor(() => expect(screen.getByText("Objectives")).toBeInTheDocument());
});

test("surfaces the canonical glyph legend trigger", () => {
  renderWithProviders(<LeftRail />, { route: "/" });
  expect(screen.getByRole("button", { name: "Status legend" })).toBeInTheDocument();
});
