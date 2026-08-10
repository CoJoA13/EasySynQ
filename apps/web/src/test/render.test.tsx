import { QueryClient, useQuery } from "@tanstack/react-query";
import { notifyManager } from "@tanstack/query-core";
import { cleanup, waitFor } from "@testing-library/react";
import { expect, test, vi } from "vitest";
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
    notifyManager.setScheduler((callback) => callback());
    cleanup();
  }
});

test("the test environment delivers TanStack Query notifications synchronously", () => {
  const onNotify = vi.fn();

  notifyManager.schedule(onNotify);

  expect(onNotify).toHaveBeenCalledTimes(1);
});
