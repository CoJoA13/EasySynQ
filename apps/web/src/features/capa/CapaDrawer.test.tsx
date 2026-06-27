import { screen, waitFor } from "@testing-library/react";
import { axe } from "jest-axe";
import { http, HttpResponse } from "msw";
import { describe, expect, it, test, vi } from "vitest";
import { TONE_GLYPH } from "../../lib/status";
import { capaDetailFixture } from "../../test/msw/handlers";
import { server } from "../../test/msw/server";
import { renderWithProviders } from "../../test/render";
import { CapaDrawer } from "./CapaDrawer";

test("renders the title, the closed-loop thread and the close gate", async () => {
  renderWithProviders(
    <CapaDrawer capaId="ca000001-0001-0001-0001-000000000001" onClose={vi.fn()} />,
  );
  expect(await screen.findByText(/Supplier re-evaluation overdue/)).toBeInTheDocument();
  // Severity rides the canonical StatusBadge (Major → warning ◔): label + accessible name + a
  // non-colour glyph (DP-7), replacing the old ad-hoc red/orange/gray Mantine colour map. Scope to
  // the badge's unique aria-label — "Major" / "◔" can appear elsewhere in the drawer body.
  const severity = screen.getByLabelText("Severity: Major");
  expect(severity).toHaveTextContent("Major");
  expect(severity).toHaveTextContent(TONE_GLYPH.warning);
  expect(screen.getByText("Closed-loop thread")).toBeInTheDocument();
  expect(screen.getByText("Raised")).toBeInTheDocument();
  expect(screen.getByText("Containment")).toBeInTheDocument();
  expect(screen.getByText(/Root cause documented/)).toBeInTheDocument();
});

test("renders the Verify→RootCause loop honestly (cycle_marker>0)", async () => {
  renderWithProviders(
    <CapaDrawer capaId="ca000005-0005-0005-0005-000000000005" onClose={vi.fn()} />,
  );
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
  renderWithProviders(
    <CapaDrawer capaId="ca000099-0099-0099-0099-000000000099" onClose={vi.fn()} />,
  );
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

// ---- S-capa-overdue: target completion date + overdue badge + inline edit ----

describe("Target completion / overdue", () => {
  it("shows the Overdue badge when overdue:true", async () => {
    server.use(
      http.get("/api/v1/capas/:id", () =>
        HttpResponse.json({
          ...capaDetailFixture,
          overdue: true,
          target_completion_date: "2026-06-01",
        }),
      ),
    );
    renderWithProviders(
      <CapaDrawer capaId="ca000001-0001-0001-0001-000000000001" onClose={vi.fn()} />,
    );
    // Badge renders with the canonical kind:label accessible name
    expect(await screen.findByLabelText("CAPA: Overdue")).toBeInTheDocument();
    // The date value is also displayed
    expect(screen.getByText("2026-06-01")).toBeInTheDocument();
  });

  it("does NOT show the Overdue badge when overdue:false", async () => {
    server.use(
      http.get("/api/v1/capas/:id", () =>
        HttpResponse.json({
          ...capaDetailFixture,
          overdue: false,
          target_completion_date: "2026-09-01",
        }),
      ),
    );
    renderWithProviders(
      <CapaDrawer capaId="ca000001-0001-0001-0001-000000000001" onClose={vi.fn()} />,
    );
    await screen.findByText("2026-09-01");
    expect(screen.queryByLabelText("CAPA: Overdue")).toBeNull();
  });

  it("seeds the date input from the CAPA's target_completion_date and resets when capaId changes", async () => {
    const capaId1 = "ca000001-0001-0001-0001-000000000001";
    const capaId2 = "ca000004-0004-0004-0004-000000000004";

    server.use(
      http.get("/api/v1/me/permissions", () =>
        HttpResponse.json({
          scope: { level: "SYSTEM", selector: null },
          permissions: [{ key: "capa.update", effect: "ALLOW", source: null }],
        }),
      ),
      http.get("/api/v1/capas/:id", ({ params }) =>
        HttpResponse.json(
          params.id === capaId2
            ? {
                ...capaDetailFixture,
                id: capaId2,
                target_completion_date: "2026-08-30",
                overdue: false,
              }
            : {
                ...capaDetailFixture,
                id: capaId1,
                target_completion_date: "2026-07-15",
                overdue: false,
              },
        ),
      ),
    );

    const { rerender } = renderWithProviders(<CapaDrawer capaId={capaId1} onClose={vi.fn()} />);
    // First CAPA: input should be seeded with its date
    await screen.findByText("Target completion");
    expect(screen.getByLabelText("Set target date")).toHaveValue("2026-07-15");

    // Switch to second CAPA: input must reset to the second CAPA's date (not stay stale)
    rerender(<CapaDrawer capaId={capaId2} onClose={vi.fn()} />);
    await waitFor(() => expect(screen.getByLabelText("Set target date")).toHaveValue("2026-08-30"));
  });

  it("shows the date edit field when the caller holds capa.update", async () => {
    server.use(
      http.get("/api/v1/me/permissions", () =>
        HttpResponse.json({
          scope: { level: "SYSTEM", selector: null },
          permissions: [{ key: "capa.update", effect: "ALLOW", source: null }],
        }),
      ),
      http.get("/api/v1/capas/:id", () =>
        HttpResponse.json({ ...capaDetailFixture, overdue: false, target_completion_date: null }),
      ),
    );
    renderWithProviders(
      <CapaDrawer capaId="ca000001-0001-0001-0001-000000000001" onClose={vi.fn()} />,
    );
    await screen.findByText("Target completion");
    // The TextInput for setting the date and a Save button must appear
    expect(screen.getByLabelText("Set target date")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Save/ })).toBeInTheDocument();
  });

  it("does NOT show the date edit field without capa.update", async () => {
    // Default permissions handler returns [] — no capa.update
    server.use(
      http.get("/api/v1/capas/:id", () =>
        HttpResponse.json({ ...capaDetailFixture, overdue: false, target_completion_date: null }),
      ),
    );
    renderWithProviders(
      <CapaDrawer capaId="ca000001-0001-0001-0001-000000000001" onClose={vi.fn()} />,
    );
    await screen.findByText("Target completion");
    expect(screen.queryByLabelText("Set target date")).toBeNull();
  });
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
        target_completion_date: null,
        overdue: false,
        stages: [
          {
            id: "s1",
            stage: "Raised",
            content_block: { problem: "x" },
            cycle_marker: 0,
            created_by: "bbbb1111-1111-1111-1111-111111111111",
            created_at: "2026-05-28T09:00:00+00:00",
            evidence_links: [],
          },
        ],
      }),
    ),
  );
  renderWithProviders(
    <CapaDrawer capaId="ca000002-0002-0002-0002-000000000002" onClose={() => {}} />,
  );
  expect(await screen.findByText("Next step")).toBeInTheDocument();
  expect(await screen.findByRole("button", { name: /Record root cause/ })).toBeInTheDocument();
});
