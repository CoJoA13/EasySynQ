import { describe, expect, test } from "vitest";
import { CAPA_COLUMNS, columnKeyFor, SEVERITY_LABEL, SOURCE_LABEL } from "./columns";

describe("columnKeyFor", () => {
  test("maps each lifecycle state to its board column", () => {
    expect(columnKeyFor("Raised")).toBe("open");
    expect(columnKeyFor("Containment")).toBe("correction");
    expect(columnKeyFor("RootCause")).toBe("rootcause");
    expect(columnKeyFor("ActionPlan")).toBe("action");
    expect(columnKeyFor("Implement")).toBe("action"); // ActionPlan + Implement merge into one column
    expect(columnKeyFor("Verify")).toBe("verify");
    expect(columnKeyFor("Closed")).toBe("closed");
    expect(columnKeyFor("Rejected")).toBe("closed"); // Rejected folds into the Closed tail
  });
});

test("CAPA_COLUMNS lists the six columns in lifecycle order", () => {
  expect(CAPA_COLUMNS.map((c) => c.key)).toEqual([
    "open", "correction", "rootcause", "action", "verify", "closed",
  ]);
});

test("severity + source labels are humanized", () => {
  expect(SEVERITY_LABEL.Critical).toBe("Critical");
  expect(SOURCE_LABEL.review_output).toBe("Mgmt review");
});
