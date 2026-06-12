import { screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, it } from "vitest";
import type { DriftStatus } from "../../lib/types";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { DoCard } from "./DoCard";

const clean = { status: "CLEAN" as const, started_at: "x", finished_at: "y", counts: {}, triggered_by: "beat" as const };
const drift: DriftStatus = {
  scans: { MIRROR: clean, BLOB_REHASH: clean },
  blob_coverage: { total: 10, never_verified: 0, failing: 0, oldest_verified_at: null },
  superseded_copies: { versions: 2, copies: 3 },
};

it("shows clean integrity, superseded copies and the ack count", async () => {
  server.use(
    http.get("/api/v1/admin/drift/status", () => HttpResponse.json(drift)),
    http.get("/api/v1/tasks", () => HttpResponse.json([{ id: "a1" }, { id: "a2" }])),
  );
  renderWithProviders(<DoCard />);
  const card = await screen.findByRole("group", { name: /do quadrant/i });
  await waitFor(() => expect(within(card).getByLabelText(/mirror & blob integrity — clean/i)).toBeInTheDocument());
  expect(within(card).getByLabelText("3 superseded copies in circulation")).toBeInTheDocument();
  expect(within(card).getByLabelText("2 acknowledgements awaiting you")).toBeInTheDocument();
  await waitFor(() => expect(within(card).getByLabelText(/status: green/i)).toBeInTheDocument());
});

it("stays visible via the self-scoped ack count even when drift is forbidden", async () => {
  server.use(
    http.get("/api/v1/admin/drift/status", () => HttpResponse.json({ code: "forbidden" }, { status: 403 })),
    http.get("/api/v1/tasks", () => HttpResponse.json([{ id: "a1" }])),
  );
  renderWithProviders(<DoCard />);
  const card = await screen.findByRole("group", { name: /do quadrant/i });
  await waitFor(() => expect(within(card).getByLabelText("1 acknowledgements awaiting you")).toBeInTheDocument());
  expect(within(card).queryByText(/no access to this section/i)).not.toBeInTheDocument();
});
