import { describe, expect, it } from "vitest";
import type { DriftStatus } from "../../lib/types";
import { driftScanFreshness, oldestStamp } from "./useHomeAsOf";

const scan = (finished_at: string | null) => ({
  status: "CLEAN" as const,
  started_at: "x",
  finished_at,
  counts: {},
  triggered_by: "beat" as const,
});
const drift = (mirror: string | null, blob: string | null): DriftStatus => ({
  scans: { MIRROR: mirror ? scan(mirror) : null, BLOB_REHASH: blob ? scan(blob) : null },
  blob_coverage: { total: 0, never_verified: 0, failing: 0, oldest_verified_at: null },
  superseded_copies: { versions: 0, copies: 0 },
});

describe("driftScanFreshness", () => {
  it("returns the OLDEST scan finished_at — the integrity signal is as fresh as its stalest scan", () => {
    const d = drift("2026-06-10T04:00:00+00:00", "2026-06-10T03:00:00+00:00");
    expect(driftScanFreshness(d)).toBe(Date.parse("2026-06-10T03:00:00+00:00"));
  });

  it("ignores a missing/unfinished scan", () => {
    expect(driftScanFreshness(drift("2026-06-10T04:00:00+00:00", null))).toBe(
      Date.parse("2026-06-10T04:00:00+00:00"),
    );
  });

  it("returns 0 (no freshness to report) when no scan has finished or there is no data", () => {
    expect(driftScanFreshness(drift(null, null))).toBe(0);
    expect(driftScanFreshness(undefined)).toBe(0);
  });
});

describe("oldestStamp", () => {
  it("returns the OLDEST (min) timestamp among successful reads — never the newest", () => {
    expect(
      oldestStamp([
        { isSuccess: true, dataUpdatedAt: 1000 },
        { isSuccess: true, dataUpdatedAt: 500 },
        { isSuccess: true, dataUpdatedAt: 800 },
      ]),
    ).toBe(500);
  });

  it("excludes a forbidden/errored read so it can't drag the stamp to 0", () => {
    expect(
      oldestStamp([
        { isSuccess: true, dataUpdatedAt: 900 },
        { isSuccess: false, dataUpdatedAt: 0 },
      ]),
    ).toBe(900);
  });

  it("excludes a never-fetched read (dataUpdatedAt 0)", () => {
    expect(oldestStamp([{ isSuccess: true, dataUpdatedAt: 0 }])).toBeNull();
  });

  it("returns null when nothing has loaded", () => {
    expect(oldestStamp([])).toBeNull();
    expect(oldestStamp([{ isSuccess: false, dataUpdatedAt: 0 }])).toBeNull();
  });
});
