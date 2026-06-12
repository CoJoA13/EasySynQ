import { screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, it } from "vitest";
import type { ComplianceChecklist, ObjectiveScorecard } from "../../lib/types";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { PlanCard } from "./PlanCard";

const scorecard = (over: Partial<ObjectiveScorecard> = {}): ObjectiveScorecard => ({
  total: 8, on_target: 6, by_rag: { green: 6, amber: 1, red: 1, unmeasured: 0 }, objectives: [], ...over,
});
const checklist = (overdue: number): ComplianceChecklist => ({
  framework: "iso9001:2015", rollup: { total: 20, covered: 18, partial: 1, gap: 1, overdue_review: overdue }, rows: [],
});

it("shows objectives on target + overdue reviews, RAG red when an objective is red", async () => {
  server.use(
    http.get("/api/v1/objectives/scorecard", () => HttpResponse.json(scorecard())),
    http.get("/api/v1/reports/compliance-checklist", () => HttpResponse.json(checklist(2))),
  );
  renderWithProviders(<PlanCard />);
  const card = await screen.findByRole("group", { name: /plan quadrant/i });
  await waitFor(() => expect(within(card).getByLabelText("6 / 8 objectives on target")).toBeInTheDocument());
  expect(within(card).getByLabelText("2 document reviews overdue")).toBeInTheDocument();
  await waitFor(() => expect(within(card).getByLabelText(/status: red/i)).toBeInTheDocument());
});

it("omits the overdue line when the checklist read is forbidden", async () => {
  server.use(
    http.get("/api/v1/objectives/scorecard", () => HttpResponse.json(scorecard({ by_rag: { green: 8, amber: 0, red: 0, unmeasured: 0 }, on_target: 8 }))),
    http.get("/api/v1/reports/compliance-checklist", () => HttpResponse.json({ code: "forbidden" }, { status: 403 })),
  );
  renderWithProviders(<PlanCard />);
  const card = await screen.findByRole("group", { name: /plan quadrant/i });
  await waitFor(() => expect(within(card).getByLabelText("8 / 8 objectives on target")).toBeInTheDocument());
  expect(within(card).queryByText(/reviews overdue/i)).not.toBeInTheDocument();
});

it("renders no-access when both reads are forbidden", async () => {
  server.use(
    http.get("/api/v1/objectives/scorecard", () => HttpResponse.json({ code: "forbidden" }, { status: 403 })),
    http.get("/api/v1/reports/compliance-checklist", () => HttpResponse.json({ code: "forbidden" }, { status: 403 })),
  );
  renderWithProviders(<PlanCard />);
  const card = await screen.findByRole("group", { name: /plan quadrant/i });
  await waitFor(() => expect(within(card).getByText(/no access to this section/i)).toBeInTheDocument());
});
