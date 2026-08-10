import { QueryClient, notifyManager, useQuery } from "@tanstack/react-query";
import { notifyManager as queryCoreNotifyManager } from "@tanstack/query-core";
import { cleanup, waitFor } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import { configureTestQueryNotifications, flushTestQueryNotifications } from "./queryNotifications";
import { renderWithProviders } from "./render";

function PendingQuery({ onAbort }: { onAbort: () => void }) {
  useQuery({
    queryKey: ["pending-test-query"],
    queryFn: ({ signal }) =>
      new Promise<never>(() => {
        signal.addEventListener("abort", onAbort, { once: true });
      }),
    retry: false,
  });
  return null;
}

test("renderWithProviders clears a pending query client when testing-library unmounts", async () => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const onAbort = vi.fn();

  renderWithProviders(<PendingQuery onAbort={onAbort} />, { queryClient });

  await waitFor(() => expect(queryClient.getQueryCache().getAll()).toHaveLength(1));
  cleanup();

  expect(onAbort).toHaveBeenCalledTimes(1);
  expect(queryClient.getQueryCache().getAll()).toHaveLength(0);
});

test("a queued query observer callback still reaches React after its render unmounts", async () => {
  const scheduled: Array<() => void> = [];
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  let resolveQuery: ((value: string) => void) | undefined;

  function SettlingQuery() {
    useQuery({
      queryKey: ["queued-query-notification"],
      queryFn: () =>
        new Promise<string>((resolve) => {
          resolveQuery = resolve;
        }),
      retry: false,
    });
    return null;
  }

  notifyManager.setScheduler((callback) => scheduled.push(callback));
  try {
    renderWithProviders(<SettlingQuery />, { queryClient });
    await waitFor(() => expect(resolveQuery).toBeTypeOf("function"));
    scheduled.length = 0;

    resolveQuery?.("settled");
    await waitFor(() =>
      expect(queryClient.getQueryData(["queued-query-notification"])).toBe("settled"),
    );
    expect(scheduled).toHaveLength(1);

    cleanup();
    const windowDescriptor = Object.getOwnPropertyDescriptor(globalThis, "window");
    delete (globalThis as { window?: Window }).window;
    try {
      expect(scheduled[0]).toThrow("window is not defined");
    } finally {
      if (windowDescriptor) Object.defineProperty(globalThis, "window", windowDescriptor);
    }
  } finally {
    configureTestQueryNotifications();
    cleanup();
  }
});

test("the test notification barrier drains queued callbacks before teardown", async () => {
  const onNotify = vi.fn();

  notifyManager.schedule(onNotify);

  expect(onNotify).not.toHaveBeenCalled();
  await flushTestQueryNotifications();
  expect(onNotify).toHaveBeenCalledTimes(1);
});

test("the React Query re-export shares TanStack Query's notification singleton", () => {
  expect(notifyManager).toBe(queryCoreNotifyManager);
});

test("the notification barrier waits for a callback microtask that schedules another notification", async () => {
  const calls: string[] = [];

  notifyManager.schedule(() => {
    calls.push("outer");
    queueMicrotask(() => {
      notifyManager.schedule(() => calls.push("microtask-nested"));
    });
  });

  await flushTestQueryNotifications();

  expect(calls).toEqual(["outer", "microtask-nested"]);
});

test("the notification barrier drains a six-deep callback microtask chain", async () => {
  const calls: string[] = [];

  notifyManager.schedule(() => {
    calls.push("outer");
    let scheduleNextNotification = () => {
      notifyManager.schedule(() => calls.push("sixth-microtask-nested"));
    };
    for (let depth = 0; depth < 6; depth += 1) {
      const next = scheduleNextNotification;
      scheduleNextNotification = () => queueMicrotask(next);
    }
    scheduleNextNotification();
  });

  await flushTestQueryNotifications();

  expect(calls).toEqual(["outer", "sixth-microtask-nested"]);
});

test("the notification barrier drains directly nested notifications", async () => {
  const calls: string[] = [];

  notifyManager.schedule(() => {
    calls.push("outer");
    notifyManager.schedule(() => calls.push("nested"));
  });

  await flushTestQueryNotifications();

  expect(calls).toEqual(["outer", "nested"]);
});

test("the notification barrier has no leak after stable quiescence", async () => {
  const onNotification = vi.fn();

  notifyManager.schedule(onNotification);
  await flushTestQueryNotifications();
  await flushTestQueryNotifications();

  expect(onNotification).toHaveBeenCalledTimes(1);
});

test("the notification barrier has no teardown hang with fake timers and no pending work", async () => {
  vi.useFakeTimers();
  try {
    await flushTestQueryNotifications();
  } finally {
    vi.useRealTimers();
  }
});

test("the notification barrier drains after fake-timer notification work advances", async () => {
  const onNotification = vi.fn();

  vi.useFakeTimers();
  try {
    notifyManager.schedule(onNotification);
    await vi.advanceTimersByTimeAsync(0);
    await flushTestQueryNotifications();
  } finally {
    vi.useRealTimers();
  }

  expect(onNotification).toHaveBeenCalledTimes(1);
});

test("a notification callback error rejects the barrier and later notifications still drain", async () => {
  const failure = new Error("scheduled callback failed");
  const onLaterNotification = vi.fn();

  notifyManager.schedule(() => {
    throw failure;
  });

  await expect(flushTestQueryNotifications()).rejects.toBe(failure);

  notifyManager.schedule(onLaterNotification);
  await flushTestQueryNotifications();

  expect(onLaterNotification).toHaveBeenCalledTimes(1);
});

test("an undefined notification failure remains observable and later notifications drain", async () => {
  const onLaterNotification = vi.fn();

  notifyManager.schedule(() => {
    throw undefined;
  });

  await expect(flushTestQueryNotifications()).rejects.toBeUndefined();

  notifyManager.schedule(onLaterNotification);
  await flushTestQueryNotifications();

  expect(onLaterNotification).toHaveBeenCalledTimes(1);
});

test("multiple notification failures are consumed by one barrier drain", async () => {
  const firstFailure = new Error("first scheduled failure");
  const secondFailure = new Error("second scheduled failure");
  const onLaterNotification = vi.fn();

  notifyManager.schedule(() => {
    throw firstFailure;
  });
  notifyManager.schedule(() => {
    throw secondFailure;
  });

  const error = await flushTestQueryNotifications().then(
    () => new Error("expected multiple notification failures"),
    (reason: unknown) => reason,
  );
  expect(error).toBeInstanceOf(AggregateError);
  expect((error as AggregateError).errors).toEqual([firstFailure, secondFailure]);

  notifyManager.schedule(onLaterNotification);
  await flushTestQueryNotifications();

  expect(onLaterNotification).toHaveBeenCalledTimes(1);
});
