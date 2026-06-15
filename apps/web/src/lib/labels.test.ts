import { describe, expect, it } from "vitest";
import { humanizeStageKey, humanizeToken } from "./labels";

describe("humanizeToken", () => {
  it("sentence-cases a snake_case token", () => {
    expect(humanizeToken("verify_failed_at")).toBe("Verify failed at");
    expect(humanizeToken("open")).toBe("Open");
  });
  it("returns the original for an empty/whitespace token", () => {
    expect(humanizeToken("")).toBe("");
  });
});

describe("humanizeStageKey", () => {
  it("drops the ':<uuid>' suffix of an MR action key", () => {
    expect(humanizeStageKey("action:9f2c1d00-0000-0000-0000-000000000001")).toBe("Action");
  });
  it("humanises a plain stage key", () => {
    expect(humanizeStageKey("approval")).toBe("Approval");
  });
});
