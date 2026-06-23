import { describe, expect, it } from "vitest";
import { allTimeZones, COMMON_ZONES, detectTimeZone, restingZones } from "./timezones";

describe("timezones", () => {
  it("detects a non-empty IANA zone string", () => {
    expect(detectTimeZone()).toMatch(/\w/);
  });

  it("allTimeZones contains UTC and is a superset of the common zones", () => {
    const all = allTimeZones();
    expect(all).toContain("UTC");
    for (const z of COMMON_ZONES) expect(all).toContain(z);
  });

  it("restingZones prepends current then detected, deduped, common zones still present", () => {
    const list = restingZones("Asia/Tokyo", "America/New_York"); // (detected, current)
    expect(list[0]).toBe("America/New_York"); // current first
    expect(list).toContain("Asia/Tokyo"); // detected included
    expect(list).toContain("UTC"); // curated still present
    expect(new Set(list).size).toBe(list.length); // no duplicates
  });
});
