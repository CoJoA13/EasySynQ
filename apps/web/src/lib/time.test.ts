import { describe, expect, it } from "vitest";
import { formatDateInTimeZone, formatOrgClock, formatRelativeTime, formatTimestamp } from "./time";

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

describe("formatOrgClock", () => {
  // The whole point of R69's clock is that it is NOT the browser's. Each case is an instant where
  // org time and UTC genuinely differ, so a regression to local/UTC time cannot pass.
  it("renders the wall-clock time observed in the organization timezone", () => {
    const instant = Date.parse("2026-06-28T15:00:00Z");
    expect(formatOrgClock(instant, "Asia/Tokyo")?.time).toBe("00:00"); // next day, and midnight
    expect(formatOrgClock(instant, "America/Chicago")?.time).toBe("10:00");
    expect(formatOrgClock(instant, "UTC")?.time).toBe("15:00");
  });

  it("renders midnight as 00:00, never 24:00", () => {
    // Some locales resolve to the h24 cycle, where midnight formats as "24:00" and reads as the
    // wrong day entirely. ⚠ The guard is `hour12: false` AND `hourCycle: "h23"` TOGETHER, and they
    // are redundant: measured, removing either alone keeps this green and removing both reddens it
    // along with three others. Asia/Tokyo above is midnight by a timezone shift; this one is
    // midnight in the zone itself.
    expect(formatOrgClock(Date.parse("2026-06-28T00:00:00Z"), "UTC")?.time).toBe("00:00");
  });

  it("honours a half-hour offset zone", () => {
    // A 30-minute offset catches an implementation that formats the hour and reuses the UTC minute.
    expect(formatOrgClock(Date.parse("2026-06-28T15:00:00Z"), "Asia/Kolkata")?.time).toBe("20:30");
  });

  it("follows the zone across a DST transition", () => {
    // Same zone, same UTC hour-of-day, six months apart: CDT in July, CST in January. A cached or
    // fixed offset would return the same time twice.
    const summer = formatOrgClock(Date.parse("2026-07-15T18:00:00Z"), "America/Chicago");
    const winter = formatOrgClock(Date.parse("2026-01-15T18:00:00Z"), "America/Chicago");
    expect(summer?.time).toBe("13:00");
    expect(winter?.time).toBe("12:00");
    expect(summer?.zone).not.toBe(winter?.zone);
  });

  it("labels the zone so the reading cannot be mistaken for local time", () => {
    expect(formatOrgClock(Date.parse("2026-06-28T15:00:00Z"), "UTC")?.zone).toBe("UTC");
  });

  // The date is the sharper half of "org time, not browser time". A wrong HOUR is a small error; a
  // wrong DAY under an "Organization date" label misstates which working day a reader is looking at,
  // and the two disagree for five hours of every day in a UTC-5 zone.
  it("resolves the DATE in the org zone, not UTC", () => {
    // 03:00Z on 2 September is still 22:00 on 1 September in Chicago, and already 12:00 on the 2nd
    // in Tokyo — one instant, three different calendar days.
    const instant = Date.parse("2026-09-02T03:00:00Z");
    expect(formatOrgClock(instant, "UTC")?.date).toBe("09/02/26");
    expect(formatOrgClock(instant, "America/Chicago")?.date).toBe("09/01/26");
    expect(formatOrgClock(instant, "Asia/Tokyo")?.date).toBe("09/02/26");
  });

  it("renders the date as a zero-padded six-digit MM/DD/YY", () => {
    // Zero padding on both the month and the day is what makes it six digits at every date; an
    // unpadded 1/5/26 would be four and the rail-foot row would change width through the year.
    expect(formatOrgClock(Date.parse("2026-01-05T12:00:00Z"), "UTC")?.date).toBe("01/05/26");
    expect(formatOrgClock(Date.parse("2026-12-31T12:00:00Z"), "UTC")?.date).toBe("12/31/26");
    const digits = formatOrgClock(Date.parse("2026-01-05T12:00:00Z"), "UTC")?.date ?? "";
    expect(digits.replace(/\D/g, "")).toHaveLength(6);
  });

  it("crosses midnight in the org zone with the date and the time together", () => {
    const beforeMidnight = formatOrgClock(Date.parse("2026-07-16T04:59:00Z"), "America/Chicago");
    const afterMidnight = formatOrgClock(Date.parse("2026-07-16T05:01:00Z"), "America/Chicago");
    expect(beforeMidnight).toMatchObject({ date: "07/15/26", time: "23:59" });
    expect(afterMidnight).toMatchObject({ date: "07/16/26", time: "00:01" });
    expect(formatOrgClock(Date.parse("2026-07-15T18:00:00Z"), "America/Chicago")?.zone).toBe("CDT");
  });

  it("returns null rather than a wrong-but-plausible time", () => {
    // LOAD-BEARING. Falling back to the browser's clock under an "Organization time" label would
    // look authoritative and be wrong; the component renders nothing when this is null.
    expect(formatOrgClock(Date.now(), undefined)).toBeNull();
    expect(formatOrgClock(Date.now(), null)).toBeNull();
    expect(formatOrgClock(Date.now(), "")).toBeNull();
    expect(formatOrgClock(Date.now(), "Not/AZone")).toBeNull();
    expect(formatOrgClock(Number.NaN, "UTC")).toBeNull();
  });
});
