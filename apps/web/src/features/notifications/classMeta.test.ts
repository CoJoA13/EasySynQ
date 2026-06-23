import { describe, expect, it } from "vitest";
import { CLASS_META } from "./classMeta";

describe("CLASS_META", () => {
  it("covers the four classes in server-enum order", () => {
    expect(CLASS_META.map((c) => c.key)).toEqual([
      "action_required",
      "awareness",
      "critical",
      "admin_ops",
    ]);
  });

  it("tags only admin_ops as in-app-only today", () => {
    expect(CLASS_META.filter((c) => c.inAppOnly).map((c) => c.key)).toEqual(["admin_ops"]);
  });

  it("gives every class a non-empty label and helper", () => {
    for (const c of CLASS_META) {
      expect(c.label.length).toBeGreaterThan(0);
      expect(c.helper.length).toBeGreaterThan(0);
    }
  });
});
