import { describe, expect, it } from "vitest";
import type { Audit, Capa, Complaint, DriftStatus, Ncr } from "../../lib/types";
import {
  capasOpenCount, complaintsAwaitingCount, countRag, coverageRag, driftRag, driftStatusText,
  ncrsAwaitingCount, openAuditsCount, overdueRag, planObjectivesRag, RAG_META, worstRag,
} from "./rag";

const drift = (over: Partial<DriftStatus> = {}): DriftStatus => ({
  scans: { MIRROR: null, BLOB_REHASH: null },
  blob_coverage: { total: 10, never_verified: 0, failing: 0, oldest_verified_at: null },
  superseded_copies: { versions: 0, copies: 0 },
  ...over,
});
const cleanScan = { started_at: "x", finished_at: "y", counts: {}, triggered_by: "beat" as const };

describe("rag rules", () => {
  it("planObjectivesRag is worst-wins (red > amber > green > neutral)", () => {
    expect(planObjectivesRag({ green: 3, amber: 0, red: 1, unmeasured: 0 })).toBe("red");
    expect(planObjectivesRag({ green: 3, amber: 1, red: 0, unmeasured: 0 })).toBe("amber");
    expect(planObjectivesRag({ green: 3, amber: 0, red: 0, unmeasured: 1 })).toBe("green");
    expect(planObjectivesRag({ green: 0, amber: 0, red: 0, unmeasured: 0 })).toBe("neutral");
  });

  it("coverageRag: gap→red, undercovered→amber, full→green", () => {
    expect(coverageRag({ total: 20, covered: 18, gap: 1 })).toBe("red");
    expect(coverageRag({ total: 20, covered: 18, gap: 0 })).toBe("amber");
    expect(coverageRag({ total: 20, covered: 20, gap: 0 })).toBe("green");
  });

  it("overdueRag + countRag", () => {
    expect(overdueRag(2)).toBe("amber");
    expect(overdueRag(0)).toBe("green");
    expect(countRag(1, "red")).toBe("red");
    expect(countRag(0, "red")).toBe("green");
  });

  it("driftRag: failing pin → red; FAILED → amber; all CLEAN → green; unscanned → neutral", () => {
    expect(driftRag(drift({ blob_coverage: { total: 1, never_verified: 0, failing: 2, oldest_verified_at: null } }))).toBe("red");
    expect(driftRag(drift({ scans: { MIRROR: { status: "DIVERGENT", ...cleanScan }, BLOB_REHASH: null } }))).toBe("red");
    expect(driftRag(drift({ scans: { MIRROR: { status: "FAILED", ...cleanScan }, BLOB_REHASH: null } }))).toBe("amber");
    expect(driftRag(drift({ scans: { MIRROR: { status: "CLEAN", ...cleanScan }, BLOB_REHASH: { status: "CLEAN", ...cleanScan } } }))).toBe("green");
    expect(driftRag(drift({ scans: { MIRROR: { status: "CLEAN", ...cleanScan }, BLOB_REHASH: null } }))).toBe("neutral");
    expect(driftRag(drift())).toBe("neutral");
  });

  it("driftStatusText", () => {
    expect(driftStatusText(drift({ scans: { MIRROR: { status: "CLEAN", ...cleanScan }, BLOB_REHASH: { status: "CLEAN", ...cleanScan } } }))).toBe("clean");
    expect(driftStatusText(drift({ blob_coverage: { total: 1, never_verified: 0, failing: 1, oldest_verified_at: null } }))).toBe("1 integrity issue");
    expect(driftStatusText(drift({ scans: { MIRROR: { status: "FAILED", ...cleanScan }, BLOB_REHASH: null } }))).toBe("scan needs attention");
  });

  it("worstRag picks the worst; empty → neutral", () => {
    expect(worstRag(["green", "red", "amber"])).toBe("red");
    expect(worstRag(["green", "neutral"])).toBe("green");
    expect(worstRag([])).toBe("neutral");
  });

  it("count helpers filter open/awaiting rows", () => {
    const audits = [{ state: "Closed" }, { state: "InProgress" }, { state: "Scheduled" }] as Audit[];
    expect(openAuditsCount(audits)).toBe(2);
    const capas = [{ close_state: "Closed" }, { close_state: "Rejected" }, { close_state: "Verify" }] as Capa[];
    expect(capasOpenCount(capas)).toBe(1);
    const ncrs = [{ disposition: null }, { disposition: "scrap" }] as Ncr[];
    expect(ncrsAwaitingCount(ncrs)).toBe(1);
    const complaints = [{ spawned_capa_id: null }, { spawned_capa_id: "x" }] as Complaint[];
    expect(complaintsAwaitingCount(complaints)).toBe(1);
  });

  it("RAG_META carries a distinct glyph + Mantine colour per RAG (DP-7)", () => {
    expect(RAG_META.green.color).toBe("green");
    expect(RAG_META.amber.color).toBe("yellow");
    expect(RAG_META.red.color).toBe("red");
    expect(new Set(Object.values(RAG_META).map((m) => m.glyph)).size).toBe(4);
  });
});
