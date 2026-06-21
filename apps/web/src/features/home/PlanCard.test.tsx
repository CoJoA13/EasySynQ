import { screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, it } from "vitest";
import type { ComplianceChecklist, ObjectiveScorecard } from "../../lib/types";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { PlanCard } from "./PlanCard";

const scorecard = (over: Partial<ObjectiveScorecard> = {}): ObjectiveScorecard => ({
  total: 8,
  on_target: 6,
  by_rag: { green: 6, amber: 1, red: 1, unmeasured: 0 },
  objectives: [],
  ...over,
});
const checklist = (overdue: number): ComplianceChecklist => ({
  framework: "iso9001:2015",
  rollup: { total: 20, covered: 18, partial: 1, gap: 1, overdue_review: overdue },
  rows: [],
});

it("shows objectives on target + overdue reviews, RAG red when an objective is red", async () => {
  server.use(
    http.get("/api/v1/objectives/scorecard", () => HttpResponse.json(scorecard())),
    http.get("/api/v1/reports/compliance-checklist", () => HttpResponse.json(checklist(2))),
  );
  renderWithProviders(<PlanCard />);
  const card = await screen.findByRole("group", { name: /plan quadrant/i });
  await waitFor(() =>
    expect(within(card).getByLabelText("6 / 8 objectives on target")).toBeInTheDocument(),
  );
  expect(within(card).getByLabelText("2 document reviews overdue")).toBeInTheDocument();
  await waitFor(() =>
    expect(within(card).getByLabelText(/status: action required/i)).toBeInTheDocument(),
  );
});

it("omits the overdue line when the checklist read is forbidden", async () => {
  server.use(
    http.get("/api/v1/objectives/scorecard", () =>
      HttpResponse.json(
        scorecard({ by_rag: { green: 8, amber: 0, red: 0, unmeasured: 0 }, on_target: 8 }),
      ),
    ),
    http.get("/api/v1/reports/compliance-checklist", () =>
      HttpResponse.json({ code: "forbidden" }, { status: 403 }),
    ),
  );
  renderWithProviders(<PlanCard />);
  const card = await screen.findByRole("group", { name: /plan quadrant/i });
  await waitFor(() =>
    expect(within(card).getByLabelText("8 / 8 objectives on target")).toBeInTheDocument(),
  );
  expect(within(card).queryByText(/reviews overdue/i)).not.toBeInTheDocument();
});

it("renders no-access only when ALL actionable reads are forbidden (risk + context + IP incl.)", async () => {
  server.use(
    http.get("/api/v1/objectives/scorecard", () =>
      HttpResponse.json({ code: "forbidden" }, { status: 403 }),
    ),
    http.get("/api/v1/reports/compliance-checklist", () =>
      HttpResponse.json({ code: "forbidden" }, { status: 403 }),
    ),
    http.get("/api/v1/risks/summary", () =>
      HttpResponse.json({ code: "forbidden" }, { status: 403 }),
    ),
    http.get("/api/v1/context/summary", () =>
      HttpResponse.json({ code: "forbidden" }, { status: 403 }),
    ),
    // The orthogonal /interested-parties/summary read must ALSO be forbidden for the no-access panel
    // to show (it folds into allForbidden — the S-context-fe orthogonal-read trap).
    http.get("/api/v1/interested-parties/summary", () =>
      HttpResponse.json({ code: "forbidden" }, { status: 403 }),
    ),
  );
  renderWithProviders(<PlanCard />);
  const card = await screen.findByRole("group", { name: /plan quadrant/i });
  await waitFor(() =>
    expect(within(card).getByText(/no access to this section/i)).toBeInTheDocument(),
  );
});

it("shows the high-risk line from the GOVERNING summary (action required when > 0)", async () => {
  server.use(
    http.get("/api/v1/objectives/scorecard", () =>
      HttpResponse.json(
        scorecard({ by_rag: { green: 8, amber: 0, red: 0, unmeasured: 0 }, on_target: 8 }),
      ),
    ),
    http.get("/api/v1/reports/compliance-checklist", () => HttpResponse.json(checklist(0))),
    // default /risks/summary fixture: published, high_risk 2
  );
  renderWithProviders(<PlanCard />);
  const card = await screen.findByRole("group", { name: /plan quadrant/i });
  await waitFor(() =>
    expect(within(card).getByLabelText("2 high / critical risks")).toBeInTheDocument(),
  );
  // a high/critical risk drives the headline to Action required even with green objectives + 0 overdue
  expect(within(card).getByLabelText(/status: action required/i)).toBeInTheDocument();
});

it("shows an honest 'no published register' line when the register is unpublished", async () => {
  server.use(
    http.get("/api/v1/objectives/scorecard", () =>
      HttpResponse.json(
        scorecard({ by_rag: { green: 8, amber: 0, red: 0, unmeasured: 0 }, on_target: 8 }),
      ),
    ),
    http.get("/api/v1/reports/compliance-checklist", () => HttpResponse.json(checklist(0))),
    http.get("/api/v1/risks/summary", () =>
      HttpResponse.json({
        published: false,
        total: 0,
        by_band: { critical: 0, high: 0, medium: 0, low: 0, unscored: 0 },
        high_risk: 0,
        by_type: { risk: 0, opportunity: 0 },
        effectiveness: { treated: 0, recorded: 0, pending: 0 },
      }),
    ),
  );
  renderWithProviders(<PlanCard />);
  const card = await screen.findByRole("group", { name: /plan quadrant/i });
  await waitFor(() =>
    expect(within(card).getByText(/no published risk register yet/i)).toBeInTheDocument(),
  );
});

it("shows the active + never-reviewed context lines from the GOVERNING summary", async () => {
  server.use(
    http.get("/api/v1/objectives/scorecard", () =>
      HttpResponse.json(
        scorecard({ by_rag: { green: 8, amber: 0, red: 0, unmeasured: 0 }, on_target: 8 }),
      ),
    ),
    http.get("/api/v1/reports/compliance-checklist", () => HttpResponse.json(checklist(0))),
    // default /context/summary fixture: published, active 4, never_reviewed 2 (the read-of-record)
  );
  renderWithProviders(<PlanCard />);
  const card = await screen.findByRole("group", { name: /plan quadrant/i });
  await waitFor(() =>
    expect(within(card).getByLabelText("4 active context issues")).toBeInTheDocument(),
  );
  expect(within(card).getByLabelText("2 context issues never reviewed")).toBeInTheDocument();
});

it("shows the active + never-reviewed interested-parties lines from the GOVERNING summary", async () => {
  server.use(
    http.get("/api/v1/objectives/scorecard", () =>
      HttpResponse.json(
        scorecard({ by_rag: { green: 8, amber: 0, red: 0, unmeasured: 0 }, on_target: 8 }),
      ),
    ),
    http.get("/api/v1/reports/compliance-checklist", () => HttpResponse.json(checklist(0))),
    // default /interested-parties/summary fixture: published, active 5, never_reviewed 2
  );
  renderWithProviders(<PlanCard />);
  const card = await screen.findByRole("group", { name: /plan quadrant/i });
  await waitFor(() =>
    expect(within(card).getByLabelText("5 active interested parties")).toBeInTheDocument(),
  );
  expect(within(card).getByLabelText("2 interested parties never reviewed")).toBeInTheDocument();
});

it("shows an honest 'no published register' line when the IP register is unpublished", async () => {
  server.use(
    http.get("/api/v1/objectives/scorecard", () =>
      HttpResponse.json(
        scorecard({ by_rag: { green: 8, amber: 0, red: 0, unmeasured: 0 }, on_target: 8 }),
      ),
    ),
    http.get("/api/v1/reports/compliance-checklist", () => HttpResponse.json(checklist(0))),
    http.get("/api/v1/interested-parties/summary", () =>
      HttpResponse.json({
        published: false,
        total: 0,
        by_party_type: {
          customer: 0,
          regulator: 0,
          supplier: 0,
          employee: 0,
          owner: 0,
          community: 0,
          partner: 0,
        },
        by_influence: { low: 0, medium: 0, high: 0, unspecified: 0 },
        by_status: { active: 0, closed: 0 },
        active: 0,
        never_reviewed: 0,
      }),
    ),
  );
  renderWithProviders(<PlanCard />);
  const card = await screen.findByRole("group", { name: /plan quadrant/i });
  await waitFor(() =>
    expect(
      within(card).getByText(/no published interested-parties register yet/i),
    ).toBeInTheDocument(),
  );
});
