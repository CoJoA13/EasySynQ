import { describe, expect, test } from "vitest";
import { daysUntil } from "./reviewDates";

describe("daysUntil", () => {
  const now = new Date(2026, 5, 10, 15, 30); // 2026-06-10 local
  test("future date counts whole days", () => {
    expect(daysUntil("2026-06-15", now)).toBe(5);
  });
  test("today is 0", () => {
    expect(daysUntil("2026-06-10", now)).toBe(0);
  });
  test("past date is negative", () => {
    expect(daysUntil("2026-06-07", now)).toBe(-3);
  });
  test("crosses month/year boundaries", () => {
    expect(daysUntil("2027-01-01", new Date(2026, 11, 31))).toBe(1);
  });
});
