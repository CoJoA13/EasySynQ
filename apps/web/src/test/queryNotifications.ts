import { notifyManager } from "@tanstack/react-query";

let pendingNotifications = 0;
let schedulingGeneration = 0;
const pendingErrors: Array<{ value: unknown }> = [];
let zeroPendingWaiters: Array<() => void> = [];
const nativeSetTimeout = globalThis.setTimeout;

function resolveZeroPendingWaiters() {
  if (pendingNotifications !== 0) return;

  const waiters = zeroPendingWaiters;
  zeroPendingWaiters = [];
  waiters.forEach((resolve) => resolve());
}

function waitForZeroPendingNotifications(): Promise<void> {
  if (pendingNotifications === 0) return Promise.resolve();

  return new Promise((resolve) => zeroPendingWaiters.push(resolve));
}

function waitForNextEventLoopTurn(): Promise<void> {
  return new Promise((resolve) => nativeSetTimeout(resolve, 0));
}

/** Keep TanStack's normal macrotask timing, but expose a stable teardown barrier for tests. */
export function configureTestQueryNotifications() {
  notifyManager.setScheduler((callback) => {
    schedulingGeneration += 1;
    pendingNotifications += 1;
    setTimeout(() => {
      try {
        callback();
      } catch (error) {
        pendingErrors.push({ value: error });
      } finally {
        pendingNotifications -= 1;
        resolveZeroPendingWaiters();
      }
    }, 0);
  });
}

export async function flushTestQueryNotifications(): Promise<void> {
  while (true) {
    await waitForZeroPendingNotifications();

    const generationAtCheckpoint = schedulingGeneration;
    await waitForNextEventLoopTurn();

    if (pendingNotifications !== 0 || schedulingGeneration !== generationAtCheckpoint) continue;

    const errors = pendingErrors.splice(0);
    if (errors.length === 1) {
      const [error] = errors;
      if (error) throw error.value;
    }
    if (errors.length > 1) {
      throw new AggregateError(
        errors.map(({ value }) => value),
        "Multiple scheduled query notifications failed",
      );
    }

    return;
  }
}
