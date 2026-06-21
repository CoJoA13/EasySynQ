import { axe } from "jest-axe";
import { screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, it } from "vitest";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { HomePage } from "./HomePage";

const forbid = () => HttpResponse.json({ code: "forbidden" }, { status: 403 });

it("renders the QMS health heading and the four PDCA quadrants, accessibly", async () => {
  const { container } = renderWithProviders(<HomePage />);
  expect(screen.getByRole("heading", { name: /qms health/i })).toBeInTheDocument();
  for (const name of [/plan quadrant/i, /do quadrant/i, /check quadrant/i, /act quadrant/i]) {
    expect(await screen.findByRole("group", { name })).toBeInTheDocument();
  }
  expect(await axe(container)).toHaveNoViolations();
});

it("under the bare-demo shape, DO + My-Tasks render while content quadrants show no-access", async () => {
  // demo holds only drift.read + self-scoped tasks; every content read 403s.
  server.use(
    http.get("/api/v1/objectives/scorecard", forbid),
    http.get("/api/v1/reports/compliance-checklist", forbid),
    http.get("/api/v1/risks/summary", forbid),
    http.get("/api/v1/context/summary", forbid),
    http.get("/api/v1/interested-parties/summary", forbid),
    http.get("/api/v1/audits", forbid),
    http.get("/api/v1/capas", forbid),
    http.get("/api/v1/ncrs", forbid),
    http.get("/api/v1/complaints", forbid),
    http.get("/api/v1/admin/drift/status", () =>
      HttpResponse.json({
        scans: {
          MIRROR: {
            status: "CLEAN",
            started_at: "x",
            finished_at: "y",
            counts: {},
            triggered_by: "beat",
          },
          BLOB_REHASH: {
            status: "CLEAN",
            started_at: "x",
            finished_at: "y",
            counts: {},
            triggered_by: "beat",
          },
        },
        blob_coverage: { total: 5, never_verified: 0, failing: 0, oldest_verified_at: null },
        superseded_copies: { versions: 0, copies: 0 },
      }),
    ),
    http.get("/api/v1/tasks", () =>
      HttpResponse.json([
        {
          id: "t1",
          instance_id: "i1",
          stage_key: "s",
          type: "DOC_ACK",
          state: "PENDING",
          assignee_user_id: null,
          candidate_pool: null,
          action_expected: null,
          due_at: null,
        },
      ]),
    ),
  );
  renderWithProviders(<HomePage />);
  const planCard = await screen.findByRole("group", { name: /plan quadrant/i });
  await waitFor(() =>
    expect(within(planCard).getByText(/no access to this section/i)).toBeInTheDocument(),
  );
  const doCard = screen.getByRole("group", { name: /do quadrant/i });
  await waitFor(() =>
    expect(within(doCard).getByLabelText(/mirror & blob integrity — clean/i)).toBeInTheDocument(),
  );
  expect(screen.getAllByText(/my tasks/i).length).toBeGreaterThan(0);
});
