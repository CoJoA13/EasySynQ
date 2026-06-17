import { screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, it } from "vitest";
import type { CapaList, ComplaintList, Initiative, NcrList } from "../../lib/types";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { ActCard } from "./ActCard";

// A mixed-stage set: Open + InProgress + Completed are "in progress" (counted = 3); Closed + Cancelled
// are excluded. Pinned `satisfies Initiative[]` to the _initiative serializer (never a hand-typed guess).
function mkInit(id: string, stage: Initiative["stage"]): Initiative {
  return {
    id,
    identifier: `IMP-2026-${id}`,
    title: `Initiative ${id}`,
    description: null,
    target_outcome: null,
    source: "manual",
    source_link_id: null,
    process_id: null,
    owner_user_id: null,
    stage,
    opened_at: "2026-06-10T09:00:00Z",
    closed_at: stage === "Closed" || stage === "Cancelled" ? "2026-06-12T09:00:00Z" : null,
    created_by: "20000000-0000-0000-0000-0000000000aa",
    created_at: "2026-06-10T09:00:00Z",
    updated_at: null,
  };
}
const mixedInitiatives = [
  mkInit("0001", "Open"),
  mkInit("0002", "InProgress"),
  mkInit("0003", "Completed"),
  mkInit("0004", "Closed"),
  mkInit("0005", "Cancelled"),
] satisfies Initiative[];

const capas: CapaList = {
  data: [
    {
      id: "c1",
      identifier: "REC-1",
      title: "x",
      source: "audit",
      severity: "Major",
      process_id: null,
      close_state: "Verify",
      cycle_marker: 0,
      origin_finding_id: null,
      raised_by: null,
      created_at: null,
    },
    {
      id: "c2",
      identifier: "REC-2",
      title: "y",
      source: "audit",
      severity: "Minor",
      process_id: null,
      close_state: "Closed",
      cycle_marker: 0,
      origin_finding_id: null,
      raised_by: null,
      created_at: null,
    },
  ],
};
const ncrs: NcrList = {
  data: [
    {
      id: "n1",
      identifier: "NCR-1",
      source: "internal",
      description: "d",
      severity: "Major",
      process_id: null,
      disposition: null,
      disposition_authorized_by: null,
      disposition_notes: null,
      disposed_at: null,
      created_at: "x",
    },
  ],
};
const complaints: ComplaintList = {
  data: [
    {
      id: "k1",
      identifier: "REC-3",
      customer: "ACME",
      received_at: null,
      channel: null,
      description: "d",
      severity: null,
      spawned_capa_id: null,
    },
  ],
};

it("shows open CAPAs, awaiting NCRs and complaints, RAG red on an awaiting NCR", async () => {
  server.use(
    http.get("/api/v1/capas", () => HttpResponse.json(capas)),
    http.get("/api/v1/ncrs", () => HttpResponse.json(ncrs)),
    http.get("/api/v1/complaints", () => HttpResponse.json(complaints)),
  );
  renderWithProviders(<ActCard />);
  const card = await screen.findByRole("group", { name: /act quadrant/i });
  // The first content assertion must wait for the query to settle (the card frame renders immediately).
  await waitFor(() => expect(within(card).getByLabelText("1 CAPAs open")).toBeInTheDocument());
  expect(within(card).getByLabelText("1 NCRs awaiting disposition")).toBeInTheDocument();
  expect(within(card).getByLabelText("1 complaints awaiting triage")).toBeInTheDocument();
  await waitFor(() =>
    expect(within(card).getByLabelText(/status: action required/i)).toBeInTheDocument(),
  );
});

it("renders no-access when all four reads are forbidden", async () => {
  // allForbidden now also requires the initiatives read to be forbidden (S-improvement-3b).
  const forbid = () => HttpResponse.json({ code: "forbidden" }, { status: 403 });
  server.use(
    http.get("/api/v1/capas", forbid),
    http.get("/api/v1/ncrs", forbid),
    http.get("/api/v1/complaints", forbid),
    http.get("/api/v1/improvement-initiatives", forbid),
  );
  renderWithProviders(<ActCard />);
  const card = await screen.findByRole("group", { name: /act quadrant/i });
  await waitFor(() =>
    expect(within(card).getByText(/no access to this section/i)).toBeInTheDocument(),
  );
});

// ---- S-improvement-3b: the "initiatives in progress" StatLine ----
it("renders the initiatives-in-progress line counting only non-terminal stages", async () => {
  server.use(
    http.get("/api/v1/improvement-initiatives", () =>
      HttpResponse.json({ data: mixedInitiatives }),
    ),
  );
  renderWithProviders(<ActCard />);
  const card = await screen.findByRole("group", { name: /act quadrant/i });
  // Open + InProgress + Completed = 3; Closed + Cancelled excluded.
  await waitFor(() =>
    expect(within(card).getByLabelText("3 initiatives in progress")).toBeInTheDocument(),
  );
});

it("the initiatives line is neutral and never raises the tile RAG above the actionable signals", async () => {
  // No actionable signals (CAPAs/NCRs/complaints all empty → green), but initiatives present. The tile
  // RAG must stay green (the initiatives line is informational, NOT pushed to the RAG fold).
  server.use(
    http.get("/api/v1/capas", () => HttpResponse.json({ data: [] })),
    http.get("/api/v1/ncrs", () => HttpResponse.json({ data: [] })),
    http.get("/api/v1/complaints", () => HttpResponse.json({ data: [] })),
    http.get("/api/v1/improvement-initiatives", () =>
      HttpResponse.json({ data: mixedInitiatives }),
    ),
  );
  renderWithProviders(<ActCard />);
  const card = await screen.findByRole("group", { name: /act quadrant/i });
  await waitFor(() =>
    expect(within(card).getByLabelText("3 initiatives in progress")).toBeInTheDocument(),
  );
  // Tile RAG = worst of the actionable signals (all green) — never amber/red from the initiatives line.
  expect(within(card).getByLabelText(/status: on track/i)).toBeInTheDocument();
  expect(within(card).queryByLabelText(/status: action required/i)).toBeNull();
  expect(within(card).queryByLabelText(/status: needs attention/i)).toBeNull();
});

it("renders the initiatives line when the 3 actionable reads are forbidden but initiatives ARE allowed", async () => {
  // allForbidden requires ALL FOUR reads forbidden — so with CAPAs/NCRs/complaints 403 but the
  // initiatives read allowed, the card does NOT show TileNoAccess; the initiatives line renders.
  const forbid = () => HttpResponse.json({ code: "forbidden" }, { status: 403 });
  server.use(
    http.get("/api/v1/capas", forbid),
    http.get("/api/v1/ncrs", forbid),
    http.get("/api/v1/complaints", forbid),
    http.get("/api/v1/improvement-initiatives", () =>
      HttpResponse.json({ data: mixedInitiatives }),
    ),
  );
  renderWithProviders(<ActCard />);
  const card = await screen.findByRole("group", { name: /act quadrant/i });
  // The initiatives-in-progress line renders (Open + InProgress + Completed = 3).
  await waitFor(() =>
    expect(within(card).getByLabelText("3 initiatives in progress")).toBeInTheDocument(),
  );
  // ...and the no-access panel is NOT shown (not all four reads are forbidden).
  expect(within(card).queryByText(/no access to this section/i)).toBeNull();
  // The forbidden actionable reads simply omit their lines (no crash).
  expect(within(card).queryByLabelText(/CAPAs open/)).toBeNull();
  expect(within(card).queryByLabelText(/NCRs awaiting disposition/)).toBeNull();
  expect(within(card).queryByLabelText(/complaints awaiting triage/)).toBeNull();
});

it("degrades calmly when the initiatives read is forbidden (line absent, no crash)", async () => {
  server.use(
    http.get("/api/v1/capas", () => HttpResponse.json({ data: [] })),
    http.get("/api/v1/ncrs", () => HttpResponse.json({ data: [] })),
    http.get("/api/v1/complaints", () => HttpResponse.json({ data: [] })),
    http.get("/api/v1/improvement-initiatives", () =>
      HttpResponse.json({ code: "forbidden" }, { status: 403 }),
    ),
  );
  renderWithProviders(<ActCard />);
  const card = await screen.findByRole("group", { name: /act quadrant/i });
  // The other (allowed) sections still render — wait on one of them to settle.
  await waitFor(() => expect(within(card).getByLabelText("0 CAPAs open")).toBeInTheDocument());
  expect(within(card).queryByLabelText(/initiatives in progress/)).toBeNull();
});
