import { describe, expect, it } from "vitest";
import { bandZones, fmtValueUnit, RAG_COLOR, RAG_GLYPH, RAG_LABEL, RAG_TONE } from "./labels";
import { TONE_GLYPH } from "../../lib/status";

describe("fmtValueUnit", () => {
  it("renders a value and unit, or an em dash when null", () => {
    expect(fmtValueUnit("92", "%")).toBe("92 %");
    expect(fmtValueUnit(null, "%")).toBe("—");
  });
});

describe("RAG maps", () => {
  it("maps every rag to a Mantine colour and a MEANING label (never the colour word)", () => {
    expect(RAG_COLOR.amber).toBe("yellow");
    // The label is the meaning a greyscale/colour-blind reader needs — not "Green"/"Amber"/"Red"
    // (DP-5; closes #144). The first three match the Home dashboard's RAG_META.label verbatim.
    expect(RAG_LABEL.green).toBe("On track");
    expect(RAG_LABEL.amber).toBe("Needs attention");
    expect(RAG_LABEL.red).toBe("Action required");
    expect(RAG_LABEL.unmeasured).toBe("Not yet measured");
  });

  it("maps every rag to its canonical status tone (badges → StatusBadge)", () => {
    // green→success ✓ (on-target), amber→warning ◔ (at-risk), red→danger ✕ (off-target),
    // unmeasured→neutral ○ (no data) — the owner design-call for the RAG → tone canon.
    expect(RAG_TONE.green).toBe("success");
    expect(RAG_TONE.amber).toBe("warning");
    expect(RAG_TONE.red).toBe("danger");
    expect(RAG_TONE.unmeasured).toBe("neutral");
  });

  it("derives each rag's non-colour glyph from the canonical TONE_GLYPH via RAG_TONE", () => {
    // ONE glyph vocabulary app-wide (the StatusBadge canon) — the chart markers + band zones source
    // this, never a second drifting set (S-obj-rag-legibility).
    expect(RAG_GLYPH.green).toBe(TONE_GLYPH.success);
    expect(RAG_GLYPH.amber).toBe(TONE_GLYPH.warning);
    expect(RAG_GLYPH.red).toBe(TONE_GLYPH.danger);
    expect(RAG_GLYPH.unmeasured).toBe(TONE_GLYPH.neutral);
  });
});

describe("bandZones", () => {
  it("HIGHER with a valid threshold below target → red|amber|green, no warning", () => {
    const m = bandZones({ target: 95, threshold: 90, direction: "HIGHER_IS_BETTER" });
    expect(m.zones).toEqual(["red", "amber", "green"]);
    expect(m.hasAmber).toBe(true);
    expect(m.warn).toBeNull();
  });

  it("HIGHER with no threshold → red|green and no amber", () => {
    const m = bandZones({ target: 95, threshold: null, direction: "HIGHER_IS_BETTER" });
    expect(m.zones).toEqual(["red", "green"]);
    expect(m.hasAmber).toBe(false);
    expect(m.warn).toBeNull();
  });

  it("HIGHER with a threshold at/above target → soft warning, no amber", () => {
    const m = bandZones({ target: 95, threshold: 96, direction: "HIGHER_IS_BETTER" });
    expect(m.zones).toEqual(["red", "green"]);
    expect(m.hasAmber).toBe(false);
    expect(m.warn).toMatch(/below the target/i);
  });

  it("LOWER with a valid threshold above target → green|amber|red", () => {
    const m = bandZones({ target: 5, threshold: 8, direction: "LOWER_IS_BETTER" });
    expect(m.zones).toEqual(["green", "amber", "red"]);
    expect(m.hasAmber).toBe(true);
    expect(m.warn).toBeNull();
  });

  it("LOWER with a threshold at/below target → soft warning, no amber", () => {
    const m = bandZones({ target: 5, threshold: 4, direction: "LOWER_IS_BETTER" });
    expect(m.zones).toEqual(["green", "red"]);
    expect(m.warn).toMatch(/above the target/i);
  });
});
