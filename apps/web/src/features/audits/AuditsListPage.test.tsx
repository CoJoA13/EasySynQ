import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe } from "jest-axe";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { AuditsListPage } from "./AuditsListPage";

test("renders honest tiles (Total / Active / Closed) from the list", async () => {
  renderWithProviders(<AuditsListPage />, { route: "/audits" });
  // 3 fixture audits: InProgress + Closing (active) and Closed.
  // Tile labels are "… audits" so they never collide with the segmented control's All/Active/Closed.
  const total = await screen.findByText("Total audits");
  expect(within(total.closest("[data-tile]") as HTMLElement).getByText("3")).toBeInTheDocument();
  const active = screen.getByText("Active audits");
  expect(within(active.closest("[data-tile]") as HTMLElement).getByText("2")).toBeInTheDocument();
  const closed = screen.getByText("Closed audits");
  expect(within(closed.closest("[data-tile]") as HTMLElement).getByText("1")).toBeInTheDocument();
});

test("table renders identifier/title/lead/state/date, newest-first; identifier links to the detail", async () => {
  renderWithProviders(<AuditsListPage />, { route: "/audits" });
  const rows = await screen.findAllByRole("row");
  // rows[0] is the header; newest created_at first → REC-000061 (2026-05-20) before REC-000066 (04-25) before REC-000055 (03-25).
  expect(within(rows[1]!).getByText("REC-000061")).toBeInTheDocument();
  expect(within(rows[1]!).getByText("Purchasing & Suppliers audit")).toBeInTheDocument();
  expect(within(rows[1]!).getByText("Mara Quality")).toBeInTheDocument(); // directory resolution
  expect(within(rows[1]!).getByText(/● In progress/)).toBeInTheDocument();
  expect(within(rows[2]!).getByText("REC-000066")).toBeInTheDocument();
  expect(within(rows[3]!).getByText("REC-000055")).toBeInTheDocument();
  // a lead the directory can't resolve degrades to a short id ("—" when null).
  expect(within(rows[3]!).getByText("—")).toBeInTheDocument();
  expect(within(rows[1]!).getByRole("link", { name: "REC-000061" })).toHaveAttribute(
    "href",
    "/audits/au000001-0001-0001-0001-000000000001",
  );
});

test("the Active/Closed segmented filter slices client-side", async () => {
  const u = userEvent.setup();
  renderWithProviders(<AuditsListPage />, { route: "/audits" });
  await screen.findByText("REC-000061");
  await u.click(screen.getByRole("radio", { name: "Closed" }));
  expect(screen.queryByText("REC-000061")).toBeNull();
  expect(screen.getByText("REC-000055")).toBeInTheDocument();
  await u.click(screen.getByRole("radio", { name: "Active" }));
  expect(screen.getByText("REC-000061")).toBeInTheDocument();
  expect(screen.queryByText("REC-000055")).toBeNull();
});

test("renders a calm no-access panel on a 403 (audit.read)", async () => {
  server.use(
    http.get("/api/v1/audits", () =>
      HttpResponse.json({ code: "forbidden", title: "Forbidden" }, { status: 403 }),
    ),
  );
  renderWithProviders(<AuditsListPage />, { route: "/audits" });
  expect(await screen.findByText(/don't have access to internal audits/)).toBeInTheDocument();
});

test("an audit title containing markup renders as literal text (XSS-safe)", async () => {
  server.use(
    http.get("/api/v1/audits", () =>
      HttpResponse.json({
        data: [{ id: "au-xss-00-0000-0000-0000-000000000000", identifier: "REC-000099", title: "<script>alert(1)</script>", plan_id: "pl000001-0001-0001-0001-000000000001", lead_auditor_user_id: null, state: "Scheduled", started_at: null, completed_at: null, result_summary: null, created_at: "2026-06-01T09:00:00+00:00" }],
      }),
    ),
  );
  renderWithProviders(<AuditsListPage />, { route: "/audits" });
  expect(await screen.findByText("<script>alert(1)</script>")).toBeInTheDocument();
});

test("no axe violations", async () => {
  const { container } = renderWithProviders(<AuditsListPage />, { route: "/audits" });
  await screen.findByText("REC-000061");
  expect(await axe(container)).toHaveNoViolations();
});
