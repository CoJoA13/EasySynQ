import { screen } from "@testing-library/react";
import { axe } from "jest-axe";
import { http, HttpResponse } from "msw";
import { describe, expect, test } from "vitest";
import { TONE_GLYPH } from "../../lib/status";
import { renderWithProviders } from "../../test/render";
import { driftStatusFixture } from "../../test/msw/handlers";
import { server } from "../../test/msw/server";
import { DriftStatusPage } from "./DriftStatusPage";

describe("DriftStatusPage", () => {
  test("renders both scan cards with status badges and the counts bag", async () => {
    renderWithProviders(<DriftStatusPage />);
    expect(await screen.findByText("Mirror scan")).toBeInTheDocument();
    expect(screen.getByText("Blob integrity")).toBeInTheDocument();
    // The canonical StatusBadge keeps the per-card accessible name AND the raw enum label verbatim.
    expect(screen.getByLabelText("Mirror scan status: CLEAN")).toBeInTheDocument();
    expect(screen.getByLabelText("Blob integrity status: DIVERGENT")).toBeInTheDocument();
    expect(screen.getByText("CLEAN")).toBeInTheDocument();
    expect(screen.getByText("DIVERGENT")).toBeInTheDocument();
    // The non-colour glyph channel (DP-5 / DP-7) survives a greyscale audit-export: CLEAN→success ✓,
    // DIVERGENT→danger ✕ (the retired ▲ is gone). Both glyphs are distinct here so getByText is unambiguous.
    expect(screen.getByText(TONE_GLYPH.success)).toBeInTheDocument();
    expect(screen.getByText(TONE_GLYPH.danger)).toBeInTheDocument();
    // counts render generically + humanised (#2b) — a MIRROR key and a BLOB_REHASH key both appear
    expect(screen.getByText("Rebuild triggered")).toBeInTheDocument();
    expect(screen.getByText("Sample limit")).toBeInTheDocument();
  });

  test("FAILED scan status maps to the danger glyph (locked owner design-call — integrity failure)", async () => {
    server.use(
      http.get("/api/v1/admin/drift/status", () =>
        HttpResponse.json({
          ...driftStatusFixture,
          scans: {
            ...driftStatusFixture.scans,
            MIRROR: { ...driftStatusFixture.scans.MIRROR!, status: "FAILED" },
            BLOB_REHASH: null,
          },
        }),
      ),
    );
    renderWithProviders(<DriftStatusPage />);
    expect(await screen.findByLabelText("Mirror scan status: FAILED")).toBeInTheDocument();
    expect(screen.getByText("FAILED")).toBeInTheDocument();
    // FAILED → danger ✕ (the strongest greyscale-safe signal for an auditor), not warning.
    expect(screen.getByText(TONE_GLYPH.danger)).toBeInTheDocument();
  });

  test("treats counts as an OPEN bag — an unknown key still renders", async () => {
    server.use(
      http.get("/api/v1/admin/drift/status", () =>
        HttpResponse.json({
          ...driftStatusFixture,
          scans: {
            ...driftStatusFixture.scans,
            MIRROR: {
              ...driftStatusFixture.scans.MIRROR!,
              counts: { ...driftStatusFixture.scans.MIRROR!.counts, brand_new_key: 7 },
            },
          },
        }),
      ),
    );
    renderWithProviders(<DriftStatusPage />);
    expect(await screen.findByText("Brand new key")).toBeInTheDocument();
    expect(screen.getByText("7")).toBeInTheDocument();
  });

  test("a never-run kind renders an honest empty card, not a crash", async () => {
    server.use(
      http.get("/api/v1/admin/drift/status", () =>
        HttpResponse.json({ ...driftStatusFixture, scans: { MIRROR: null, BLOB_REHASH: null } }),
      ),
    );
    renderWithProviders(<DriftStatusPage />);
    expect(await screen.findAllByText("Never run yet.")).toHaveLength(2);
  });

  test("failing > 0 surfaces the unresolved-findings alarm with the live count", async () => {
    renderWithProviders(<DriftStatusPage />);
    // Structural: the alarm is a real alert element carrying the fixture's live failing count.
    const alarm = await screen.findByRole("alert");
    expect(alarm).toHaveTextContent(/unresolved integrity findings — re-alarming until restored/);
    expect(alarm).toHaveTextContent(String(driftStatusFixture.blob_coverage.failing));
  });

  test("failing = 0 shows no alarm", async () => {
    server.use(
      http.get("/api/v1/admin/drift/status", () =>
        HttpResponse.json({
          ...driftStatusFixture,
          blob_coverage: { ...driftStatusFixture.blob_coverage, failing: 0 },
        }),
      ),
    );
    renderWithProviders(<DriftStatusPage />);
    await screen.findByText("Mirror scan");
    expect(screen.queryByText(/unresolved integrity findings/)).not.toBeInTheDocument();
  });

  test("D4 headline links to the superseded-copies tab", async () => {
    renderWithProviders(<DriftStatusPage />);
    const link = await screen.findByRole("link", { name: /view the report/i });
    expect(link).toHaveAttribute("href", "/drift/superseded-copies");
  });

  test("403 renders the calm no-access panel", async () => {
    server.use(
      http.get("/api/v1/admin/drift/status", () =>
        HttpResponse.json({ code: "forbidden", title: "Forbidden" }, { status: 403 }),
      ),
    );
    renderWithProviders(<DriftStatusPage />);
    expect(await screen.findByText("No access")).toBeInTheDocument();
  });

  test("has no axe violations", async () => {
    const { container } = renderWithProviders(<DriftStatusPage />);
    await screen.findByText("Mirror scan");
    expect(await axe(container)).toHaveNoViolations();
  });
});
