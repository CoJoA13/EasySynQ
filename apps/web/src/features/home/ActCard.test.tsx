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
  truncated: false,
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
      target_completion_date: null,
      overdue: false,
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
      target_completion_date: null,
      overdue: false,
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
    expect(
      within(within(card).getByRole("group", { name: "ACT signal" })).getByText(
        /status: action required/i,
      ),
    ).toBeInTheDocument(),
  );
});

it("renders no-access when the actionable reads are forbidden, even though the initiatives read returns data", async () => {
  // ACT no-access is governed by the ACTIONABLE reads (CAPA/NCR/complaint) only. The initiatives list is
  // auth-only / filter-not-403 (it returns a filtered/empty 200, never a 403), so it must NOT keep the
  // tile out of TileNoAccess (Codex P2 regression guard). Even with initiatives data present, all three
  // actionable reads forbidden → no access, and the initiatives line is suppressed.
  const forbid = () => HttpResponse.json({ code: "permission_denied" }, { status: 403 });
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
  await waitFor(() =>
    expect(within(card).getByText(/no access to this section/i)).toBeInTheDocument(),
  );
  // The initiatives line must NOT leak through the no-access state.
  expect(within(card).queryByLabelText(/initiatives in progress/)).toBeNull();
  // Nor may the HEADER, which folds the same observations. Asserted directly on the signal band
  // rather than via the card's accessible names: S-ui-3 removed the header's aria-label, which is
  // what the queryByLabelText above used to reach — so without this the leak became unguarded.
  const band = within(card).getByRole("group", { name: "ACT signal" });
  expect(within(band).queryByText(/initiatives in progress/)).not.toBeInTheDocument();
  expect(within(band).queryByText(/status:/i)).not.toBeInTheDocument();
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
  const signal = within(card).getByRole("group", { name: "ACT signal" });
  expect(within(signal).getByText(/status: on track/i)).toBeInTheDocument();
  expect(within(signal).queryByText(/status: action required/i)).toBeNull();
  // And the severity actually announced is the remapped one — RAG_META.neutral.label is "No data",
  // which would contradict the real count shown beside it.
  expect(within(signal).queryByText(/no data/i)).not.toBeInTheDocument();
  // Was queryByLabelText, which matched the removed StatusBadge's aria-label and so could no longer
  // fail; the severity is now plain text inside the band.
  expect(within(signal).queryByText(/status: needs attention/i)).not.toBeInTheDocument();
});

it("shows the initiatives line alongside a partially-accessible tile (one actionable read available)", async () => {
  // A user with SOME ACT access (CAPAs readable) but the others forbidden: the tile is NOT no-access,
  // and the initiatives line renders beside the available CAPA line. Guards the additive-line behaviour
  // without conflating the initiatives read with tile access.
  const forbid = () => HttpResponse.json({ code: "permission_denied" }, { status: 403 });
  server.use(
    http.get("/api/v1/capas", () => HttpResponse.json({ data: [] })),
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
  // ...and the no-access panel is NOT shown (the CAPA read succeeded).
  expect(within(card).queryByText(/no access to this section/i)).toBeNull();
  expect(within(card).getByLabelText("0 CAPAs open")).toBeInTheDocument();
  // The forbidden actionable reads simply omit their lines (no crash).
  expect(within(card).queryByLabelText(/NCRs awaiting disposition/)).toBeNull();
  expect(within(card).queryByLabelText(/complaints awaiting triage/)).toBeNull();
});

it("degrades calmly when the initiatives read is forbidden (line absent, no crash)", async () => {
  server.use(
    http.get("/api/v1/capas", () => HttpResponse.json({ data: [] })),
    http.get("/api/v1/ncrs", () => HttpResponse.json({ data: [] })),
    http.get("/api/v1/complaints", () => HttpResponse.json({ data: [] })),
    http.get("/api/v1/improvement-initiatives", () =>
      HttpResponse.json({ code: "permission_denied" }, { status: 403 }),
    ),
  );
  renderWithProviders(<ActCard />);
  const card = await screen.findByRole("group", { name: /act quadrant/i });
  // The other (allowed) sections still render — wait on one of them to settle.
  await waitFor(() => expect(within(card).getByLabelText("0 CAPAs open")).toBeInTheDocument());
  expect(within(card).queryByLabelText(/initiatives in progress/)).toBeNull();
});

it("marks the CAPA count as a floor when the register scan window was truncated", async () => {
  // [Audit U14] This KPI counts client-side over the CAPA register, whose pre-authorization scan
  // window is capped server-side. When the API reports `truncated`, an exact-looking compliance
  // number would UNDER-REPORT — the tile must present it as a floor instead.
  server.use(
    http.get("/api/v1/capas", () =>
      HttpResponse.json({ ...capas, truncated: true } satisfies CapaList),
    ),
    http.get("/api/v1/ncrs", () => HttpResponse.json(ncrs)),
    http.get("/api/v1/complaints", () => HttpResponse.json(complaints)),
  );
  renderWithProviders(<ActCard />);
  const card = await screen.findByRole("group", { name: /act quadrant/i });
  await waitFor(() => expect(within(card).getByLabelText("1+ CAPAs open")).toBeInTheDocument());
  expect(within(card).queryByLabelText("1 CAPAs open")).not.toBeInTheDocument();
});

it("the header names something the tile actually shows", async () => {
  // The invariant the derived signal exists to guarantee: the header is folded from the same
  // observations the StatLines render, so its text must correspond to a line the reader can see.
  // Drift — a card pushing an observation whose value or label differs from its StatLine — is
  // invisible to every other test here, because each asserts the header and the lines separately.
  //
  // Scope, stated honestly: this reaches the DRIVING observation only, since that is the sole one
  // the header can express. Verified by mutating it in both directions (label and value); mutating
  // a NON-driving observation does not fail this test, and cannot, because nothing renders it.
  renderWithProviders(<ActCard />);
  const card = await screen.findByRole("group", { name: /act quadrant/i });
  const band = within(card).getByRole("group", { name: "ACT signal" });
  await waitFor(() => expect(within(card).getByLabelText(/CAPAs open/)).toBeInTheDocument());

  // The header's observation text, with the visually-hidden severity stripped off.
  const headerText = (band.textContent ?? "").replace(/Status:.*$/, "").trim();
  const observation = headerText
    .replace(/^ACT\s*Cl 10\s*/, "")
    .replace(/^[^0-9A-Za-z]+/, "")
    .trim();
  expect(observation.length).toBeGreaterThan(0);

  // StatLine exposes "<value> <label>" as its accessible name — the same string the header builds.
  const lineNames = within(card)
    .getAllByRole("group")
    .map((g) => g.getAttribute("aria-label"))
    .filter((n): n is string => Boolean(n));
  expect(
    lineNames,
    `header said "${observation}" but the tile shows ${JSON.stringify(lineNames)}`,
  ).toContain(observation);
});
