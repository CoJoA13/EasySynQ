import { screen, waitFor, within } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, it } from "vitest";
import type { CapaList, ComplaintList, NcrList } from "../../lib/types";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { ActCard } from "./ActCard";

const capas: CapaList = {
  data: [
    { id: "c1", identifier: "REC-1", title: "x", source: "audit", severity: "Major", process_id: null, close_state: "Verify", cycle_marker: 0, origin_finding_id: null, raised_by: null, created_at: null },
    { id: "c2", identifier: "REC-2", title: "y", source: "audit", severity: "Minor", process_id: null, close_state: "Closed", cycle_marker: 0, origin_finding_id: null, raised_by: null, created_at: null },
  ],
};
const ncrs: NcrList = {
  data: [
    { id: "n1", identifier: "NCR-1", source: "internal", description: "d", severity: "Major", process_id: null, disposition: null, disposition_authorized_by: null, disposition_notes: null, disposed_at: null, created_at: "x" },
  ],
};
const complaints: ComplaintList = {
  data: [
    { id: "k1", identifier: "REC-3", customer: "ACME", received_at: null, channel: null, description: "d", severity: null, spawned_capa_id: null },
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
  await waitFor(() => expect(within(card).getByLabelText(/status: red/i)).toBeInTheDocument());
});

it("renders no-access when all three reads are forbidden", async () => {
  const forbid = () => HttpResponse.json({ code: "forbidden" }, { status: 403 });
  server.use(
    http.get("/api/v1/capas", forbid),
    http.get("/api/v1/ncrs", forbid),
    http.get("/api/v1/complaints", forbid),
  );
  renderWithProviders(<ActCard />);
  const card = await screen.findByRole("group", { name: /act quadrant/i });
  await waitFor(() => expect(within(card).getByText(/no access to this section/i)).toBeInTheDocument());
});
