import { describe, expect, it } from "vitest";
import { oldestStamp } from "./useHomeAsOf";

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
