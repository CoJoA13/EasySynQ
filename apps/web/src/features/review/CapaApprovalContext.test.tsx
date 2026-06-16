import { screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, test } from "vitest";
import { TONE_GLYPH } from "../../lib/status";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { capaApprovalFixture } from "../../test/msw/handlers";
import { CapaApprovalContext } from "./CapaApprovalContext";

test("shows the CAPA identity + the proposed action plan being approved", async () => {
  server.use(http.get("/api/v1/capas/:id/approval", () => HttpResponse.json(capaApprovalFixture)));
  renderWithProviders(<CapaApprovalContext capaId="ca000001-0001-0001-0001-000000000001" />, {
    route: "/tasks/x",
  });
  expect(await screen.findByText(/REC-000031/)).toBeInTheDocument();
  // Severity rides the canonical StatusBadge (Major → warning ◔): label + accessible name + a
  // non-colour glyph carry the meaning, not colour alone (DP-7).
  expect(await screen.findByText("Major")).toBeInTheDocument();
  expect(screen.getByLabelText("Severity: Major")).toBeInTheDocument();
  expect(screen.getByText(TONE_GLYPH.warning)).toBeInTheDocument();
  expect(await screen.findByText(/Proposed action plan/)).toBeInTheDocument();
  expect(await screen.findByText(/Schedule supplier re-evaluations/)).toBeInTheDocument();
});
