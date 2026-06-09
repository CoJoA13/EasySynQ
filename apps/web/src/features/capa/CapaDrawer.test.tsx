import { screen } from "@testing-library/react";
import { axe } from "jest-axe";
import { http, HttpResponse } from "msw";
import { expect, test, vi } from "vitest";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { CapaDrawer } from "./CapaDrawer";

test("renders the title, the closed-loop thread and the close gate", async () => {
  renderWithProviders(<CapaDrawer capaId="ca000001-0001-0001-0001-000000000001" onClose={vi.fn()} />);
  expect(await screen.findByText(/Supplier re-evaluation overdue/)).toBeInTheDocument();
  expect(screen.getByText("Closed-loop thread")).toBeInTheDocument();
  expect(screen.getByText("Raised")).toBeInTheDocument();
  expect(screen.getByText("Containment")).toBeInTheDocument();
  expect(screen.getByText(/Root cause documented/)).toBeInTheDocument();
});

test("renders the Verify→RootCause loop honestly (cycle_marker>0)", async () => {
  renderWithProviders(<CapaDrawer capaId="ca000005-0005-0005-0005-000000000005" onClose={vi.fn()} />);
  // the loop fixture carries multiple current-cycle (cycle 1) stages, each labelled "Cycle 2"
  const loopLabels = await screen.findAllByText(/Cycle 2/);
  expect(loopLabels.length).toBeGreaterThan(0);
});

test("surfaces a calm error (not an endless spinner) when the detail load fails", async () => {
  server.use(
    http.get("/api/v1/capas/:id", () =>
      HttpResponse.json({ code: "not_found", title: "Not found" }, { status: 404 }),
    ),
  );
  renderWithProviders(<CapaDrawer capaId="ca000099-0099-0099-0099-000000000099" onClose={vi.fn()} />);
  expect(await screen.findByText(/Couldn't load this CAPA/)).toBeInTheDocument();
});

test("is closed (renders no dialog) when capaId is null", () => {
  const { container } = renderWithProviders(<CapaDrawer capaId={null} onClose={vi.fn()} />);
  expect(container.querySelector('[role="dialog"]')).toBeNull();
});

test("no axe violations when open", async () => {
  const { container } = renderWithProviders(
    <CapaDrawer capaId="ca000001-0001-0001-0001-000000000001" onClose={vi.fn()} />,
  );
  await screen.findByText(/Supplier re-evaluation overdue/);
  expect(await axe(container)).toHaveNoViolations();
});

test("renders the Advance panel form for the caller's permitted stage", async () => {
  server.use(
    http.get("/api/v1/me/permissions", () =>
      HttpResponse.json({
        scope: { level: "PROCESS", selector: null },
        permissions: [{ key: "capa.record_rca", effect: "ALLOW", source: null }],
      }),
    ),
  );
  // Use a Containment-state CAPA to exercise the root-cause form:
  server.use(
    http.get("/api/v1/capas/:id", () =>
      HttpResponse.json({
        id: "ca000002-0002-0002-0002-000000000002",
        identifier: "REC-000034",
        title: "Containment-state CAPA",
        source: "complaint",
        severity: "Critical",
        process_id: "pr1",
        close_state: "Containment",
        cycle_marker: 0,
        origin_finding_id: null,
        raised_by: "bbbb1111-1111-1111-1111-111111111111",
        created_at: "2026-05-28T09:00:00+00:00",
        stages: [
          { id: "s1", stage: "Raised", content_block: { problem: "x" }, cycle_marker: 0, created_by: "bbbb1111-1111-1111-1111-111111111111", created_at: "2026-05-28T09:00:00+00:00", evidence_links: [] },
        ],
      }),
    ),
  );
  renderWithProviders(<CapaDrawer capaId="ca000002-0002-0002-0002-000000000002" onClose={() => {}} />);
  expect(await screen.findByRole("button", { name: /Record root cause/ })).toBeInTheDocument();
});
