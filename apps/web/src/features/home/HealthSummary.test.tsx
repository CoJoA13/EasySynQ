import { screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, it } from "vitest";
import type { ComplianceChecklist } from "../../lib/types";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { HealthSummary } from "./HealthSummary";

const checklist: ComplianceChecklist = {
  framework: "iso9001:2015",
  rollup: { total: 20, covered: 18, partial: 1, gap: 1, overdue_review: 2 },
  rows: [],
};

it("shows the mandatory-coverage status with the N9 microcopy, linking to /compliance", async () => {
  server.use(http.get("/api/v1/reports/compliance-checklist", () => HttpResponse.json(checklist)));
  renderWithProviders(<HealthSummary />);
  await waitFor(() => expect(screen.getByText(/18 \/ 20 mandatory items current/i)).toBeInTheDocument());
  expect(screen.getByText(/not a compliance verdict/i)).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /coverage/i })).toHaveAttribute("href", "/compliance");
});

it("degrades calmly when coverage is forbidden", async () => {
  server.use(http.get("/api/v1/reports/compliance-checklist", () => HttpResponse.json({ code: "forbidden" }, { status: 403 })));
  renderWithProviders(<HealthSummary />);
  await waitFor(() => expect(screen.getByText(/coverage scoped to your access/i)).toBeInTheDocument());
});
