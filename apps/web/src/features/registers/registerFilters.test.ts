import { describe, expect, it } from "vitest";

import {
  buildRegisterParams,
  hasActiveRegisterFilters,
  registerFilterKey,
  registerQuerySuffix,
} from "./registerFilters";

describe("register filter serialization", () => {
  it("emits the bracketed grammar the API narrows on", () => {
    const params = buildRegisterParams({ createdFrom: "2026-01-01" });
    expect(params.get("filter[created_at][gte]")).toBe("2026-01-01");
  });

  it("extends an upper bound to the end of the named day", () => {
    // A bare date parses as midnight, so `lte=2026-03-05` would exclude everything created
    // during the 5th — the opposite of what the operator picked.
    const params = buildRegisterParams({ createdTo: "2026-03-05" });
    expect(params.get("filter[created_at][lte]")).toBe("2026-03-05T23:59:59");
  });

  it("omits a cleared control instead of sending it blank", () => {
    // The API refuses an unparseable value with 422, so a blank parameter would turn clearing a
    // filter into an error rather than a reset.
    const params = buildRegisterParams({ createdFrom: "" });
    expect([...params.keys()]).toEqual([]);
    expect(registerQuerySuffix({})).toBe("");
  });

  it("keys the cache order-independently", () => {
    const a = registerFilterKey({ createdFrom: "2026-01-01", createdTo: "2026-02-01" });
    const b = registerFilterKey({ createdTo: "2026-02-01", createdFrom: "2026-01-01" });
    expect(a).toBe(b);
    expect(a).not.toBe(registerFilterKey({ createdFrom: "2026-01-02" }));
  });

  it("reports whether anything is selected", () => {
    expect(hasActiveRegisterFilters({})).toBe(false);
    expect(hasActiveRegisterFilters({ createdFrom: "" })).toBe(false);
    expect(hasActiveRegisterFilters({ createdFrom: "2026-01-01" })).toBe(true);
  });
});
