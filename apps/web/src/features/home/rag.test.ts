import { describe, expect, it } from "vitest";
import type { Audit, Capa, Complaint, DriftStatus, Ncr } from "../../lib/types";
import {
  RAG_META,
  capasOpenCount,
  complaintsAwaitingCount,
  countRag,
  coverageRag,
  driftRag,
  driftStatusText,
  ncrsAwaitingCount,
  openAuditsCount,
  overdueRag,
  planObjectivesRag,
  quadrantSignal,
  worstRag,
} from "./rag";
import type { Rag } from "./rag";

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
    expect(
      driftRag(
        drift({
          blob_coverage: { total: 1, never_verified: 0, failing: 2, oldest_verified_at: null },
        }),
      ),
    ).toBe("red");
    expect(
      driftRag(
        drift({ scans: { MIRROR: { status: "DIVERGENT", ...cleanScan }, BLOB_REHASH: null } }),
      ),
    ).toBe("red");
    expect(
      driftRag(drift({ scans: { MIRROR: { status: "FAILED", ...cleanScan }, BLOB_REHASH: null } })),
    ).toBe("amber");
    expect(
      driftRag(
        drift({
          scans: {
            MIRROR: { status: "CLEAN", ...cleanScan },
            BLOB_REHASH: { status: "CLEAN", ...cleanScan },
          },
        }),
      ),
    ).toBe("green");
    expect(
      driftRag(drift({ scans: { MIRROR: { status: "CLEAN", ...cleanScan }, BLOB_REHASH: null } })),
    ).toBe("neutral");
    expect(driftRag(drift())).toBe("neutral");
  });

  it("driftStatusText", () => {
    expect(
      driftStatusText(
        drift({
          scans: {
            MIRROR: { status: "CLEAN", ...cleanScan },
            BLOB_REHASH: { status: "CLEAN", ...cleanScan },
          },
        }),
      ),
    ).toBe("clean");
    expect(
      driftStatusText(
        drift({
          blob_coverage: { total: 1, never_verified: 0, failing: 1, oldest_verified_at: null },
        }),
      ),
    ).toBe("1 integrity issue");
    expect(
      driftStatusText(
        drift({ scans: { MIRROR: { status: "FAILED", ...cleanScan }, BLOB_REHASH: null } }),
      ),
    ).toBe("scan needs attention");
  });

  it("worstRag picks the worst; empty → neutral", () => {
    expect(worstRag(["green", "red", "amber"])).toBe("red");
    expect(worstRag(["green", "neutral"])).toBe("green");
    expect(worstRag([])).toBe("neutral");
  });

  it("count helpers filter open/awaiting rows", () => {
    const audits = [
      { state: "Closed" },
      { state: "InProgress" },
      { state: "Scheduled" },
    ] as Audit[];
    expect(openAuditsCount(audits)).toBe(2);
    const capas = [
      { close_state: "Closed" },
      { close_state: "Rejected" },
      { close_state: "Verify" },
    ] as Capa[];
    expect(capasOpenCount(capas)).toBe(1);
    const ncrs = [{ disposition: null }, { disposition: "scrap" }] as Ncr[];
    expect(ncrsAwaitingCount(ncrs)).toBe(1);
    const complaints = [{ spawned_capa_id: null }, { spawned_capa_id: "x" }] as Complaint[];
    expect(complaintsAwaitingCount(complaints)).toBe(1);
  });

  it("RAG_META maps each RAG to a canonical tone + a distinct glyph (DP-7)", () => {
    expect(RAG_META.green.tone).toBe("success");
    expect(RAG_META.amber.tone).toBe("warning");
    expect(RAG_META.red.tone).toBe("danger");
    expect(RAG_META.neutral.tone).toBe("neutral");
    expect(new Set(Object.values(RAG_META).map((m) => m.glyph)).size).toBe(4);
  });
});

describe("quadrantSignal — the header states an observation, never a verdict", () => {
  const RAG_VERDICT_WORDS = ["On track", "Needs attention", "Action required", "No data"];

  it("names the count that DROVE the severity, not a summary of it", () => {
    // The regression this exists for: an ACT header reading "on track" above six open CAPAs.
    const signal = quadrantSignal([
      { value: 6, label: "CAPAs open", rag: "amber" },
      { value: 0, label: "NCRs awaiting disposition", rag: "green" },
    ]);
    expect(signal.rag).toBe("amber");
    expect(signal.text).toBe("6 CAPAs open");
  });

  it("reports the worst signal when several are raised", () => {
    const signal = quadrantSignal([
      { value: 6, label: "CAPAs open", rag: "amber" },
      { value: 2, label: "NCRs awaiting disposition", rag: "red" },
    ]);
    expect(signal.rag).toBe("red");
    expect(signal.text).toBe("2 NCRs awaiting disposition");
  });

  it("still states a count when everything is within threshold", () => {
    // Not "all clear" and not "on track" — an observed zero.
    const signal = quadrantSignal([{ value: 0, label: "CAPAs open", rag: "green" }]);
    expect(signal.rag).toBe("green");
    expect(signal.text).toBe("0 CAPAs open");
  });

  it("says it has no data rather than presenting absence as absence of problems", () => {
    const signal = quadrantSignal([]);
    expect(signal.rag).toBe("neutral");
    expect(signal.text).toBe("no data");
  });

  it("preserves a truncated count as the floor the tile shows", () => {
    // U14: register scans are capped, so the tile renders "12+". The header must not round that
    // into a confident exact number.
    const signal = quadrantSignal([{ value: "12+", label: "CAPAs open", rag: "amber" }]);
    expect(signal.text).toBe("12+ CAPAs open");
  });

  it("never emits a RAG verdict word for ANY combination of observations", () => {
    // The rule, enforced mechanically rather than by reading the strings. If someone later swaps
    // the derived text back to RAG_META[rag].label, every case here fails at once.
    const rags: Rag[] = ["green", "amber", "red", "neutral"];
    for (const a of rags) {
      for (const b of rags) {
        const signal = quadrantSignal([
          { value: 3, label: "CAPAs open", rag: a },
          { value: 1, label: "NCRs awaiting disposition", rag: b },
        ]);
        for (const word of RAG_VERDICT_WORDS) {
          expect(signal.text, `${a}+${b} produced a verdict`).not.toContain(word);
        }
      }
    }
  });

  it("carries a non-colour glyph for every severity", () => {
    for (const rag of ["green", "amber", "red"] as const) {
      const signal = quadrantSignal([{ value: 1, label: "x", rag }]);
      expect(signal.glyph).toBe(RAG_META[rag].glyph);
      expect(signal.glyph.length).toBeGreaterThan(0);
    }
  });
});

describe("quadrantSignal with a valueless status observation", () => {
  it("renders the label alone rather than an empty count", () => {
    // The tiles render status lines with no number ("no published risk register yet"). Interpolating
    // an absent value would produce a leading "undefined " or a stray space in the header.
    const signal = quadrantSignal([
      { label: "no published risk & opportunity register yet", rag: "neutral" },
    ]);
    expect(signal.text).toBe("no published risk & opportunity register yet");
    expect(signal.text).not.toMatch(/undefined|^\s/);
  });

  it("still prefers the driving observation when a status line coexists with a count", () => {
    const signal = quadrantSignal([
      { label: "no published context register yet", rag: "neutral" },
      { value: 4, label: "document reviews overdue", rag: "amber" },
    ]);
    expect(signal.text).toBe("4 document reviews overdue");
  });
});

describe("quadrantSignal statusLabel — 'no data' and 'informational' are different things", () => {
  it("says No data only when nothing was observed", () => {
    expect(quadrantSignal([]).statusLabel).toBe("No data");
  });

  it("says Informational when a neutral count WAS observed", () => {
    // Reusing RAG_META.neutral.label here would have a screen reader announce "No data" beside
    // "3 initiatives in progress". StatLine makes the same remap for the same reason.
    const s = quadrantSignal([{ value: 3, label: "initiatives in progress", rag: "neutral" }]);
    expect(s.text).toBe("3 initiatives in progress");
    expect(s.statusLabel).toBe("Informational");
  });

  it("keeps the RAG word for every actionable severity", () => {
    for (const rag of ["green", "amber", "red"] as const) {
      expect(quadrantSignal([{ value: 1, label: "x", rag }]).statusLabel).toBe(RAG_META[rag].label);
    }
  });
});
