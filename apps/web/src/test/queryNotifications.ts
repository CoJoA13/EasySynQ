import { notifyManager } from "@tanstack/react-query";

let pendingNotifications = 0;
let schedulingGeneration = 0;
const pendingErrors: unknown[] = [];
let zeroPendingWaiters: Array<() => void> = [];

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

/** Keep TanStack's normal macrotask timing, but expose a stable teardown barrier for tests. */
export function configureTestQueryNotifications() {
  notifyManager.setScheduler((callback) => {
    schedulingGeneration += 1;
    pendingNotifications += 1;
    setTimeout(() => {
      try {
        callback();
      } catch (error) {
        pendingErrors.push(error);
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
    await Promise.resolve();

    if (pendingNotifications !== 0 || schedulingGeneration !== generationAtCheckpoint) continue;

    const error = pendingErrors.shift();
    if (error !== undefined) throw error;

    return;
  }
}
