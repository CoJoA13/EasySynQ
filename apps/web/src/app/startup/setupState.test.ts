import { afterEach, expect, test, vi } from "vitest";
import {
  SETUP_STATE_TIMEOUT_MS,
  fetchSetupState,
  parseSetupState,
  type SetupState,
} from "./setupState";

test.each(["UNINITIALIZED", "IN_SETUP", "OPERATIONAL"] as const)(
  "accepts the published %s state",
  (setup_state: SetupState) => {
    expect(parseSetupState({ setup_state })).toEqual({ setup_state });
  },
);

test.each([null, [], {}, { setup_state: null }, { setup_state: 1 }, { setup_state: "UNKNOWN" }])(
  "rejects untrusted setup-state payload %#",
  (payload) => {
    expect(() => parseSetupState(payload)).toThrow("invalid setup state response");
  },
);

interface Deferred<T> {
  promise: Promise<T>;
  resolve: (value: T | PromiseLike<T>) => void;
  reject: (reason?: unknown) => void;
}

function deferred<T>(): Deferred<T> {
  let resolve!: Deferred<T>["resolve"];
  let reject!: Deferred<T>["reject"];
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function expectSetupStateFetch(fetchMock: ReturnType<typeof vi.fn>) {
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/v1/setup/state",
    expect.objectContaining({ signal: expect.any(AbortSignal) }),
  );
}

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

test("rejects when the setup-state request fails on the network", async () => {
  const fetchMock = vi
    .spyOn(globalThis, "fetch")
    .mockRejectedValue(new Error("network details must not leak"));

  await expect(fetchSetupState()).rejects.toThrow();
  expectSetupStateFetch(fetchMock);
});

test("rejects non-successful setup-state responses with a generic error", async () => {
  const fetchMock = vi
    .spyOn(globalThis, "fetch")
    .mockResolvedValue(new Response(null, { status: 503 }));

  await expect(fetchSetupState()).rejects.toThrow("setup state request failed");
  expectSetupStateFetch(fetchMock);
});

test("rejects when the setup-state response is not valid JSON", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("not json"));

  await expect(fetchSetupState()).rejects.toThrow();
  expectSetupStateFetch(fetchMock);
});

test("rejects decoded setup-state values outside the published enum", async () => {
  const fetchMock = vi
    .spyOn(globalThis, "fetch")
    .mockResolvedValue(Response.json({ setup_state: "UNKNOWN" }));

  await expect(fetchSetupState()).rejects.toThrow("invalid setup state response");
  expectSetupStateFetch(fetchMock);
});

test("rejects when the caller cancels the setup-state request", async () => {
  const response = deferred<Response>();
  const fetchMock = vi.spyOn(globalThis, "fetch").mockReturnValue(response.promise);
  const caller = new AbortController();
  const attempt = fetchSetupState(caller.signal);

  expectSetupStateFetch(fetchMock);
  caller.abort();

  await expect(attempt).rejects.toThrow("Setup state request cancelled");
  expect(fetchMock.mock.calls[0]?.[1]?.signal).not.toBe(caller.signal);
  response.resolve(Response.json({ setup_state: "OPERATIONAL" }));
});

test("cleans up its deadline after a successful setup-state response", async () => {
  vi.useFakeTimers();
  const fetchMock = vi
    .spyOn(globalThis, "fetch")
    .mockResolvedValue(Response.json({ setup_state: "OPERATIONAL" }));

  await expect(fetchSetupState()).resolves.toEqual({
    setup_state: "OPERATIONAL",
  });
  expect(vi.getTimerCount()).toBe(0);
  expectSetupStateFetch(fetchMock);
});

test("rejects at the exact setup-state request deadline", async () => {
  vi.useFakeTimers();
  const response = deferred<Response>();
  const fetchMock = vi.spyOn(globalThis, "fetch").mockReturnValue(response.promise);

  const attempt = fetchSetupState();
  const rejected = expect(attempt).rejects.toThrow("setup state request timed out");
  await vi.advanceTimersByTimeAsync(SETUP_STATE_TIMEOUT_MS - 1);
  expect(vi.getTimerCount()).toBe(1);
  await vi.advanceTimersByTimeAsync(1);
  await rejected;
  expect(vi.getTimerCount()).toBe(0);
  expectSetupStateFetch(fetchMock);

  response.resolve(Response.json({ setup_state: "OPERATIONAL" }));
  await vi.runAllTicks();
  await expect(attempt).rejects.toThrow("setup state request timed out");
});
