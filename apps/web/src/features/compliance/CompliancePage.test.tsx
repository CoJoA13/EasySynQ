import { screen, within } from "@testing-library/react";
import { axe } from "jest-axe";
import { http, HttpResponse } from "msw";
import { describe, expect, test } from "vitest";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { CompliancePage } from "./CompliancePage";

test("renders the rollup + ★ rows with a clause drill-through link", async () => {
  renderWithProviders(<CompliancePage />, { route: "/compliance" });
  expect(await screen.findByText("External providers")).toBeInTheDocument();
  // the 8.4 GAP row's clause cell links to the filtered Library
  const link = screen.getByRole("link", { name: /8.4/ });
  expect(link).toHaveAttribute("href", "/library?clause=8.4");
  // a GAP badge is present
  expect(screen.getByLabelText("Coverage: Gap")).toBeInTheDocument();
});

test("renders a calm no-access panel on a 403 (not a crash)", async () => {
  server.use(
    http.get("/api/v1/reports/compliance-checklist", () =>
      HttpResponse.json({ code: "forbidden", title: "Forbidden" }, { status: 403 }),
    ),
  );
  renderWithProviders(<CompliancePage />, { route: "/compliance" });
  expect(await screen.findByText(/don’t have access/)).toBeInTheDocument();
});

test("has no axe violations (rows + 403)", async () => {
  const ok = renderWithProviders(<CompliancePage />, { route: "/compliance" });
  await screen.findByText("External providers");
  expect(await axe(ok.container)).toHaveNoViolations();
  ok.unmount();

  server.use(
    http.get("/api/v1/reports/compliance-checklist", () =>
      HttpResponse.json({ code: "forbidden", title: "Forbidden" }, { status: 403 }),
    ),
  );
  const forbidden = renderWithProviders(<CompliancePage />, { route: "/compliance" });
  await screen.findByText(/don’t have access/);
  expect(await axe(forbidden.container)).toHaveNoViolations();
});

describe("overdue-review leg (S-web-8)", () => {
  test("rollup shows the overdue-review counter", async () => {
    renderWithProviders(<CompliancePage />, { route: "/compliance" });
    expect(await screen.findByText(/Review overdue: 1/)).toBeInTheDocument();
  });

  test("an overdue row gets the badge; others render a dash", async () => {
    renderWithProviders(<CompliancePage />, { route: "/compliance" });
    await screen.findByText(/4\.3/);
    const overdueRow = screen.getByText(/4\.3/).closest("tr")!;
    expect(within(overdueRow).getByLabelText("Review overdue")).toBeInTheDocument();
    const cleanRow = screen.getByText(/6\.2/).closest("tr")!;
    expect(within(cleanRow).queryByLabelText("Review overdue")).not.toBeInTheDocument();
  });

  test("overdue is orthogonal — the 4.3 row is still COVERED", async () => {
    renderWithProviders(<CompliancePage />, { route: "/compliance" });
    await screen.findByText(/4\.3/);
    const row = screen.getByText(/4\.3/).closest("tr")!;
    expect(within(row).getByLabelText("Coverage: Covered")).toBeInTheDocument();
    expect(within(row).getByLabelText("Review overdue")).toBeInTheDocument();
  });
});
