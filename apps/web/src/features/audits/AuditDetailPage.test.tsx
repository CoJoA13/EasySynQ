import { screen } from "@testing-library/react";
import { axe } from "jest-axe";
import { http, HttpResponse } from "msw";
import { Route, Routes } from "react-router-dom";
import { expect, test } from "vitest";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { AuditDetailPage } from "./AuditDetailPage";

function harness(id: string) {
  return renderWithProviders(
    <Routes>
      <Route path="/audits/:id" element={<AuditDetailPage />} />
    </Routes>,
    { route: `/audits/${id}` },
  );
}

test("renders header (identifier · title · state) + plan/programme context", async () => {
  harness("au000001-0001-0001-0001-000000000001");
  expect(await screen.findByText("REC-000061")).toBeInTheDocument();
  expect(screen.getByText("Purchasing & Suppliers audit")).toBeInTheDocument();
  // getAllBy: once Task 13 mounts the stepper, "● In progress" appears twice (badge + current node).
  expect(screen.getAllByText(/● In progress/).length).toBeGreaterThan(0);
  expect(screen.getByText("Mara Quality")).toBeInTheDocument(); // lead via directory
  // Plan context: scheduled date + checklist ref + auditee process + the programme title.
  expect(await screen.findByText(/2026-05-28/)).toBeInTheDocument();
  expect(screen.getByText(/FRM-AUD-002/)).toBeInTheDocument();
  expect(screen.getByText(/Purchasing$/)).toBeInTheDocument();
  expect(screen.getByText(/2026 Internal Audit Programme/)).toBeInTheDocument();
});

test("404 → a calm not-found panel", async () => {
  harness("au-missing-0000-0000-0000-000000000000");
  expect(await screen.findByText(/Audit not found/)).toBeInTheDocument();
});

test("403 → a calm no-access panel", async () => {
  server.use(
    http.get("/api/v1/audits/:id", () =>
      HttpResponse.json({ code: "forbidden", title: "Forbidden" }, { status: 403 }),
    ),
  );
  harness("au000001-0001-0001-0001-000000000001");
  expect(await screen.findByText(/don't have access to internal audits/)).toBeInTheDocument();
});

test("no axe violations", async () => {
  const { container } = harness("au000001-0001-0001-0001-000000000001");
  await screen.findByText("REC-000061");
  expect(await axe(container)).toHaveNoViolations();
});
