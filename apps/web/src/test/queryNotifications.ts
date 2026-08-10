import { notifyManager } from "@tanstack/query-core";

let pendingNotifications = 0;
let drainWaiters: Array<() => void> = [];

function resolveDrainWaiters() {
  if (pendingNotifications !== 0) return;

  const waiters = drainWaiters;
  drainWaiters = [];
  waiters.forEach((resolve) => resolve());
}

/** Keep TanStack's normal macrotask timing, but expose a precise teardown barrier for tests. */
export function configureTestQueryNotifications() {
  notifyManager.setScheduler((callback) => {
    pendingNotifications += 1;
    setTimeout(() => {
      try {
        callback();
      } finally {
        pendingNotifications -= 1;
        resolveDrainWaiters();
      }
    }, 0);
  });
}

export function flushTestQueryNotifications(): Promise<void> {
  if (pendingNotifications === 0) return Promise.resolve();

  return new Promise((resolve) => drainWaiters.push(resolve));
}
