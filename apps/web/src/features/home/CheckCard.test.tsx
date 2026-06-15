import { screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, it } from "vitest";
import type { AuditList, ComplianceChecklist } from "../../lib/types";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { CheckCard } from "./CheckCard";

const audits: AuditList = {
  data: [
    {
      id: "a1",
      identifier: "REC-1",
      title: "Q2 audit",
      plan_id: "p1",
      lead_auditor_user_id: null,
      state: "InProgress",
      started_at: null,
      completed_at: null,
      result_summary: null,
      created_at: null,
    },
    {
      id: "a2",
      identifier: "REC-2",
      title: "Q1 audit",
      plan_id: "p2",
      lead_auditor_user_id: null,
      state: "Closed",
      started_at: null,
      completed_at: null,
      result_summary: null,
      created_at: null,
    },
  ],
};
const checklist: ComplianceChecklist = {
  framework: "iso9001:2015",
  rollup: { total: 20, covered: 18, partial: 1, gap: 1, overdue_review: 0 },
  rows: [],
};

it("shows open audits + coverage, RAG red on a gap", async () => {
  server.use(
    http.get("/api/v1/audits", () => HttpResponse.json(audits)),
    http.get("/api/v1/reports/compliance-checklist", () => HttpResponse.json(checklist)),
  );
  renderWithProviders(<CheckCard />);
  const card = await screen.findByRole("group", { name: /check quadrant/i });
  // The first content assertion must wait for the query to settle (the card frame renders immediately).
  await waitFor(() => expect(within(card).getByLabelText("1 open audits")).toBeInTheDocument());
  expect(within(card).getByLabelText("18 / 20 mandatory clauses covered")).toBeInTheDocument();
  // The next-review line rides the global next-due handler (due_soon / 2026-06-01).
  await waitFor(() =>
    expect(within(card).getByText("Next management review due 2026-06-01")).toBeInTheDocument(),
  );
  // The gap RAG (red) still wins worst-of, with due_soon (amber) folded in.
  await waitFor(() =>
    expect(within(card).getByLabelText(/status: action required/i)).toBeInTheDocument(),
  );
});

it("renders no-access when all reads are forbidden", async () => {
  const forbid = () => HttpResponse.json({ code: "forbidden" }, { status: 403 });
  server.use(
    http.get("/api/v1/audits", forbid),
    http.get("/api/v1/reports/compliance-checklist", forbid),
    http.get("/api/v1/management-reviews/next-due", forbid),
  );
  renderWithProviders(<CheckCard />);
  const card = await screen.findByRole("group", { name: /check quadrant/i });
  await waitFor(() =>
    expect(within(card).getByText(/no access to this section/i)).toBeInTheDocument(),
  );
});
