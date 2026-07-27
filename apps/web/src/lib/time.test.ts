import { describe, expect, it } from "vitest";
import { formatDateInTimeZone, formatRelativeTime, formatTimestamp } from "./time";

describe("formatDateInTimeZone", () => {
  it("renders a UTC instant on the organization calendar date", () => {
    expect(formatDateInTimeZone("2026-06-28T15:00:00Z", "Asia/Tokyo")).toBe("2026-06-29");
    expect(formatDateInTimeZone("2026-07-01T02:00:00Z", "America/Chicago")).toBe("2026-06-30");
  });

  it("fails safely when the timezone or timestamp is unavailable", () => {
    expect(formatDateInTimeZone("2026-06-28T15:00:00Z", undefined)).toBe("2026-06-28");
    expect(formatDateInTimeZone("2026-06-28T15:00:00Z", "Mars/Phobos")).toBe("2026-06-28");
    expect(formatDateInTimeZone("not-a-date", "Asia/Tokyo")).toBe("not-a-date");
  });
});

describe("formatRelativeTime", () => {
  const now = Date.parse("2026-06-15T12:00:00Z");

  it("returns 'just now' under a minute (and for a future/skewed stamp)", () => {
    expect(formatRelativeTime(now - 5_000, now)).toBe("just now");
    expect(formatRelativeTime(now + 60_000, now)).toBe("just now");
  });

  it("buckets minutes and hours", () => {
    expect(formatRelativeTime(now - 5 * 60_000, now)).toBe("5 min ago");
    expect(formatRelativeTime(now - 3 * 3_600_000, now)).toBe("3 h ago");
  });

  it("buckets days, with 'yesterday' at one day", () => {
    expect(formatRelativeTime(now - 24 * 3_600_000, now)).toBe("yesterday");
    expect(formatRelativeTime(now - 3 * 24 * 3_600_000, now)).toBe("3 days ago");
  });

  it("falls back to an absolute timestamp beyond a week", () => {
    const out = formatRelativeTime(now - 30 * 24 * 3_600_000, now);
    expect(out).toMatch(/2026/);
  });

  it("accepts an ISO string", () => {
    expect(formatRelativeTime("2026-06-15T11:55:00Z", now)).toBe("5 min ago");
  });

  it("returns an empty string for an unparseable input", () => {
    expect(formatRelativeTime("not-a-date", now)).toBe("");
  });
});

describe("formatTimestamp", () => {
  it("renders a timezone-explicit absolute string (year + a zone token)", () => {
    const out = formatTimestamp("2026-06-15T12:00:00Z");
    expect(out).toMatch(/2026/);
    // timeZoneName:"short" always emits a zone token (UTC / GMT±n / an abbreviation).
    expect(out).toMatch(/UTC|GMT|[A-Z]{2,5}/);
  });
});
