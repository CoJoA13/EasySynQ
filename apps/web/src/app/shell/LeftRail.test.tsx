import { QueryClient } from "@tanstack/react-query";
import { screen, waitFor, within } from "@testing-library/react";
import { axe } from "jest-axe";
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

test("Library + Review and approve sit under the DO section", async () => {
  renderWithProviders(<LeftRail />, { route: "/library" });
  const doSection = screen.getByRole("group", { name: "DO section" });
  expect(within(doSection).getByRole("link", { name: "Library" })).toHaveAttribute(
    "href",
    "/library",
  );
  // Settle-aware and prefix-matched ON PURPOSE. The /tasks entry appends its open-task count to its
  // own accessible name once the count query lands, so an exact-name assertion here would pass only
  // while the query was still in flight — a green that depends on timing, not on placement. This
  // test is about PLACEMENT; the count semantics are pinned by the three tests below.
  await waitFor(() =>
    expect(within(doSection).getByRole("link", { name: /^Review and approve/ })).toHaveAttribute(
      "href",
      "/tasks",
    ),
  );
  expect(within(doSection).getByRole("link", { name: "Records" })).toHaveAttribute(
    "href",
    "/records",
  );
});

test("the /tasks entry announces its open-task count, so AT gets what the badge shows", async () => {
  // The default handler returns one task.
  renderWithProviders(<LeftRail />, { route: "/" });
  expect(
    await screen.findByRole("link", { name: "Review and approve, 1 open task" }),
  ).toHaveAttribute("href", "/tasks");
});

test("a true zero shows no badge and leaves the name bare", async () => {
  server.use(http.get("/api/v1/tasks", () => HttpResponse.json([])));
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  renderWithProviders(<LeftRail />, { route: "/", queryClient });

  // Synchronise on the LANDED DATA, not on the DOM. The pending rail renders the bare label too
  // (taskCountLabel returns the base for kind:"pending"), so findByRole with the bare name resolves
  // on the first poll — before the empty response arrives — and the assertions below would then run
  // against the pre-settle rail and prove nothing about the zero case. The cache is the
  // deterministic signal, matching the ["clauses"] pattern used elsewhere in this file.
  await waitFor(() => expect(queryClient.getQueryData(["my-tasks"])).toEqual([]));

  const link = screen.getByRole("link", { name: "Review and approve" });
  expect(link).toHaveAttribute("href", "/tasks");
  // Nothing to flag — and specifically not a "0", in the badge OR the accessible name.
  expect(within(link).queryByText("0")).not.toBeInTheDocument();
  expect(link.getAttribute("aria-label")).not.toMatch(/\b0\b/);
});

test("a FAILED count never renders as zero (the never-a-confident-zero rule)", async () => {
  server.use(http.get("/api/v1/tasks", () => new HttpResponse(null, { status: 500 })));
  renderWithProviders(<LeftRail />, { route: "/" });
  // The name says the count is unavailable rather than silently reading as "no open tasks",
  // which is what a bare label or a "0" badge would both imply.
  const link = await screen.findByRole("link", {
    name: "Review and approve, task count unavailable",
  });
  expect(within(link).queryByText("0")).not.toBeInTheDocument();
});

test("every rail entry carries an aria-hidden icon that does not pollute its accessible name", async () => {
  renderWithProviders(<LeftRail />, { route: "/" });
  const home = await screen.findByRole("link", { name: "Home" });
  // The trap this guards: an icon rendered inside the NavLink WITHOUT aria-hidden becomes part of
  // the accessible name, and every exact-name query in this file stops matching.
  const svg = home.querySelector("svg");
  expect(svg).not.toBeNull();
  expect(svg).toHaveAttribute("aria-hidden", "true");
  for (const link of screen.getAllByRole("link")) {
    expect(link.querySelector("svg"), `${link.textContent} has no icon`).not.toBeNull();
  }
});

test("Records is unconditional because PROCESS-scoped record.read is not visible to the SYSTEM permissions query", async () => {
  renderWithProviders(<LeftRail />, { route: "/" });
  expect(await screen.findByRole("link", { name: "Records" })).toHaveAttribute("href", "/records");
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

test("the rail exposes no clause-filter links and never fetches /clauses (removed — every exact-match top-level filter returned zero documents)", async () => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  renderWithProviders(<LeftRail />, { route: "/library", queryClient });
  await screen.findByText(/PLAN ·/);
  // Settle-aware: the OLD rail mounted its clause links only after the async useClauses()
  // fetch landed, so a pre-fetch DOM negative would pass against reverted code too. The
  // ["clauses"] query never being REGISTERED is the deterministic revert-RED signal (and pins
  // the "no /clauses call on every page load" claim).
  await waitFor(() => expect(queryClient.isFetching()).toBe(0));
  expect(queryClient.getQueryState(["clauses"])).toBeUndefined();
  expect(
    screen
      .queryAllByRole("link")
      .some((a) => a.getAttribute("href")?.startsWith("/library?clause=")),
  ).toBe(false);
  expect(screen.queryByRole("button", { name: /Clauses/ })).not.toBeInTheDocument();
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

test("the Document register entry is always shown (a PROCESS-scoped report.read holder needs it too; the page handles 403)", async () => {
  // default MSW /me/permissions returns no key at all — the entry must still render (mirrors the
  // Risk/Context/Internal-audit ungated precedent above).
  renderWithProviders(<LeftRail />, { route: "/" });
  const link = await screen.findByRole("link", { name: "Document register" });
  expect(link).toHaveAttribute("href", "/reports/document-control");
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
  renderWithProviders(<LeftRail />, { route: "/imports" });
  const link = await screen.findByRole("link", { name: "Import" });
  expect(link).toHaveAttribute("href", "/imports");
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

test("the rail has no axe violations once its count has landed", async () => {
  const { container } = renderWithProviders(<LeftRail />, { route: "/" });
  // Settle-aware ON PURPOSE. Auditing before the count resolves would audit a DIFFERENT rail: no
  // badge, no count in the accessible name, and the permission-gated entries not yet mounted. The
  // interesting state for an audit is the fully populated one.
  await screen.findByRole("link", { name: /^Review and approve,/ });
  expect(await axe(container)).toHaveNoViolations();
});

test("the PDCA phase hue is decoration, never the only carrier of the grouping", async () => {
  // DP-5: colour alone must not encode meaning. The marker bar is aria-hidden and the phase NAME
  // sits beside it as text, so the grouping survives with colour removed entirely.
  renderWithProviders(<LeftRail />, { route: "/" });
  const plan = await screen.findByRole("group", { name: "PLAN section" });
  expect(within(plan).getByText(/^PLAN ·/)).toBeInTheDocument();
  // Anchored to the marker's own element. A bare [aria-hidden="true"] selector matches the first
  // rail ICON instead — every icon is an aria-hidden <svg> with no text — so removing the marker
  // entirely would still have satisfied it. The marker is the div carrying the phase hue.
  const marker = plan.querySelector<HTMLElement>('div[aria-hidden="true"]');
  expect(marker, "the phase marker bar is missing").not.toBeNull();
  expect(marker!.style.background).toContain("--es-plan");
  // If the bar ever became the only signal, this is the assertion that would have to be deleted.
  expect(marker).not.toHaveTextContent(/\w/);
});
