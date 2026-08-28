import { screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { expect, it } from "vitest";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { useOrgDate } from "./useOrgDate";

// [Audit U20 / the C11 class] Registers and timelines used `new Date(iso).toISOString().slice(0,10)`,
// which reports the UTC calendar day — the wrong day for every org east or west of UTC. A probe
// component pins the org-timezone formatting the shared hook now applies.

function Probe({ iso }: { iso: string }) {
  const formatDate = useOrgDate();
  return <span data-testid="out">{formatDate(iso)}</span>;
}

function meWithTimezone(tz: string) {
  server.use(
    http.get("/api/v1/me", () =>
      HttpResponse.json({
        id: "11111111-1111-1111-1111-111111111111",
        display_name: "Mara",
        email: "mara@example.com",
        org_id: "22222222-2222-2222-2222-222222222222",
        org_timezone: tz,
      }),
    ),
  );
}

it("renders a late-evening UTC instant as the NEXT day in a positive-offset org", async () => {
  meWithTimezone("Australia/Sydney");
  renderWithProviders(<Probe iso="2026-06-01T23:30:00Z" />);
  // Already 2026-06-02 in Sydney (UTC+10) — the UTC slice would have said 2026-06-01.
  await waitFor(() => expect(screen.getByTestId("out")).toHaveTextContent("2026-06-02"));
});

it("renders an early-morning UTC instant as the PREVIOUS day in a negative-offset org", async () => {
  meWithTimezone("America/Denver");
  renderWithProviders(<Probe iso="2026-06-02T02:00:00Z" />);
  // Still 2026-06-01 in Denver (UTC-6) — the UTC slice would have said 2026-06-02.
  await waitFor(() => expect(screen.getByTestId("out")).toHaveTextContent("2026-06-01"));
});
