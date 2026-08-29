import { describe, expect, it } from "vitest";
import { resolveTaskCount, taskCountBadge, taskCountLabel } from "./taskCount";

const q = (over: Partial<Parameters<typeof resolveTaskCount>[0]> = {}) => ({
  data: undefined,
  isError: false,
  isLoading: false,
  forbidden: false,
  ...over,
});

describe("resolveTaskCount", () => {
  it("reports a known non-zero count", () => {
    expect(resolveTaskCount(q({ data: [1, 2, 3] }))).toEqual({ kind: "count", count: 3 });
  });

  it("reports a true zero as 'none', distinct from 'unavailable'", () => {
    expect(resolveTaskCount(q({ data: [] }))).toEqual({ kind: "none" });
  });

  it("says nothing while the first load is in flight", () => {
    expect(resolveTaskCount(q({ isLoading: true }))).toEqual({ kind: "pending" });
  });

  it("treats an error as unavailable, never as zero", () => {
    expect(resolveTaskCount(q({ isError: true }))).toEqual({ kind: "unavailable" });
  });

  it("treats a 403 as unavailable, never as zero", () => {
    expect(resolveTaskCount(q({ forbidden: true }))).toEqual({ kind: "unavailable" });
  });

  it("does NOT present a stale count as current when a refetch fails", () => {
    // The state that is awkward to stage over the network and easy to get wrong: TanStack keeps the
    // previously fetched `data` while setting isError on a failed refetch. Reading data.length here
    // would show yesterday's number as if it were today's.
    expect(resolveTaskCount(q({ data: [1, 2, 3], isError: true }))).toEqual({
      kind: "unavailable",
    });
  });

  it("prefers 'unavailable' over 'pending' when a load is both in flight and already failed", () => {
    expect(resolveTaskCount(q({ isLoading: true, isError: true }))).toEqual({
      kind: "unavailable",
    });
  });
});

describe("taskCountBadge", () => {
  it("caps a large count so the rail cannot be widened by a number", () => {
    expect(taskCountBadge({ kind: "count", count: 100 })).toBe("99+");
    expect(taskCountBadge({ kind: "count", count: 99 })).toBe("99");
  });

  it("renders no badge for pending or a true zero", () => {
    expect(taskCountBadge({ kind: "pending" })).toBeNull();
    expect(taskCountBadge({ kind: "none" })).toBeNull();
  });

  it("never renders the digit 0", () => {
    for (const state of [
      { kind: "none" } as const,
      { kind: "pending" } as const,
      { kind: "unavailable" } as const,
    ]) {
      expect(taskCountBadge(state)).not.toBe("0");
    }
  });
});

describe("taskCountLabel", () => {
  it("adds the count to the accessible name so it is not visual-only", () => {
    expect(taskCountLabel("Review and approve", { kind: "count", count: 3 })).toBe(
      "Review and approve, 3 open tasks",
    );
  });

  it("says 'task' for exactly one", () => {
    expect(taskCountLabel("Review and approve", { kind: "count", count: 1 })).toBe(
      "Review and approve, 1 open task",
    );
  });

  it("names the failure rather than staying silent about it", () => {
    // Silence would read identically to "you have no open tasks".
    expect(taskCountLabel("Review and approve", { kind: "unavailable" })).toBe(
      "Review and approve, task count unavailable",
    );
  });

  it("leaves the name bare while pending and on a true zero", () => {
    expect(taskCountLabel("Review and approve", { kind: "pending" })).toBe("Review and approve");
    expect(taskCountLabel("Review and approve", { kind: "none" })).toBe("Review and approve");
  });
});

describe("the offline / paused first load", () => {
  it("is pending, not a confident zero", () => {
    // TanStack reports isLoading === false when a query is PAUSED (offline, fetchStatus "paused")
    // because nothing is in flight. Keying the pending test on isLoading therefore let an offline
    // first load fall through to `data?.length ?? 0` and render "no open tasks" to a user who is
    // simply disconnected. The absence of DATA is the honest test.
    expect(resolveTaskCount(q({ isLoading: false, data: undefined }))).toEqual({ kind: "pending" });
  });

  it("stays pending regardless of what isLoading claims", () => {
    for (const isLoading of [true, false]) {
      expect(resolveTaskCount(q({ isLoading, data: undefined }))).toEqual({ kind: "pending" });
    }
  });
});
