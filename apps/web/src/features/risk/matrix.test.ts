import { describe, expect, it } from "vitest";
import { bandForCell, cellRating } from "./matrix";

// The FE golden mirror of the backend `default_criteria(MATRIX_5X5)` band thresholds
// (apps/api/.../tests/unit/test_risk_rules.py is the backend golden). If the backend ever changes the
// v1 5×5 thresholds in place, BOTH goldens fail — forcing the mint-a-new-scoring_method path.
describe("risk matrix band thresholds (v1 5x5_matrix)", () => {
  it("rating = likelihood × severity", () => {
    expect(cellRating(4, 5)).toBe(20);
    expect(cellRating(1, 1)).toBe(1);
    expect(cellRating(5, 5)).toBe(25);
  });

  it("bands a cell against the v1 thresholds (critical ≥20, high ≥12, medium ≥6, low ≥1)", () => {
    // boundary cells
    expect(bandForCell(4, 5)).toBe("critical"); // 20
    expect(bandForCell(5, 5)).toBe("critical"); // 25
    expect(bandForCell(3, 4)).toBe("high"); // 12
    expect(bandForCell(4, 4)).toBe("high"); // 16
    expect(bandForCell(2, 3)).toBe("medium"); // 6
    expect(bandForCell(2, 5)).toBe("medium"); // 10
    expect(bandForCell(1, 1)).toBe("low"); // 1
    expect(bandForCell(1, 5)).toBe("low"); // 5
  });

  it("is total over the whole 5×5 grid (every cell bands to a real band, never unscored)", () => {
    for (let l = 1; l <= 5; l++) {
      for (let s = 1; s <= 5; s++) {
        expect(bandForCell(l, s)).not.toBe("unscored");
      }
    }
  });
});
