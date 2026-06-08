import { expect, test } from "vitest";
import {
  QUEUES,
  buildFilesQuery,
  parseRunUrl,
  queueToFilesQuery,
} from "./filters";

test("QUEUES lists the five tabs in mockup order", () => {
  expect(QUEUES.map((q) => q.value)).toEqual(["needs", "medium", "high", "quarantine", "vault"]);
});

test("queueToFilesQuery maps each queue to the server-supported /files filter", () => {
  expect(queueToFilesQuery("needs")).toEqual({ review_status: "undecided" });
  expect(queueToFilesQuery("medium")).toEqual({ band: "MEDIUM" });
  expect(queueToFilesQuery("high")).toEqual({ band: "HIGH" });
  expect(queueToFilesQuery("quarantine")).toEqual({ disposition: "quarantine" });
  // "vault" has no clean /files filter (v1 partial) → no server filter.
  expect(queueToFilesQuery("vault")).toEqual({});
});

test("a confidence override narrows the band within a queue", () => {
  expect(queueToFilesQuery("needs", "LOW")).toEqual({ review_status: "undecided", band: "LOW" });
});

test("parseRunUrl reads queue + confidence + offset with safe defaults", () => {
  const a = parseRunUrl(new URLSearchParams("queue=high&conf=MEDIUM&offset=200"));
  expect(a).toEqual({ queue: "high", conf: "MEDIUM", offset: 200 });
  const b = parseRunUrl(new URLSearchParams(""));
  expect(b).toEqual({ queue: "needs", conf: "ALL", offset: 0 });
  // a bogus queue/conf/offset degrades to the default
  const c = parseRunUrl(new URLSearchParams("queue=bogus&conf=bogus&offset=-3"));
  expect(c).toEqual({ queue: "needs", conf: "ALL", offset: 0 });
});

test("buildFilesQuery serializes the filter + pagination", () => {
  const qs = buildFilesQuery({ band: "HIGH", review_status: "undecided" }, { limit: 100, offset: 0 });
  const p = new URLSearchParams(qs);
  expect(p.get("band")).toBe("HIGH");
  expect(p.get("review_status")).toBe("undecided");
  expect(p.get("limit")).toBe("100");
  expect(p.get("offset")).toBe("0");
});
