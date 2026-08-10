import { notifyManager } from "@tanstack/react-query";

interface TrackedNotification {
  callback: () => void;
  cancelTimer: () => void;
}

const MAX_DRAINED_NOTIFICATIONS = 10_000;
const MAX_STABILITY_TURNS = 100;
const pendingNotifications = new Set<TrackedNotification>();
let schedulingGeneration = 0;
const pendingErrors: Array<{ value: unknown }> = [];
const nativeSetTimeout = globalThis.setTimeout;

function runTrackedNotification(notification: TrackedNotification): void {
  if (!pendingNotifications.delete(notification)) return;

  try {
    notification.callback();
  } catch (error) {
    pendingErrors.push({ value: error });
  }
}

function cancelPendingNotifications(): void {
  const notifications = [...pendingNotifications];
  pendingNotifications.clear();
  notifications.forEach((notification) => notification.cancelTimer());
}

function throwPendingErrors(): void {
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
}

function waitForNextEventLoopTurn(): Promise<void> {
  return new Promise((resolve) => nativeSetTimeout(resolve, 0));
}

/** Keep TanStack's normal macrotask timing, but expose a stable teardown barrier for tests. */
export function configureTestQueryNotifications() {
  notifyManager.setScheduler((callback) => {
    schedulingGeneration += 1;
    const scheduleTimer = globalThis.setTimeout;
    const cancelTimer = globalThis.clearTimeout;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const notification: TrackedNotification = {
      callback,
      cancelTimer: () => {
        if (timer === null) return;
        cancelTimer(timer);
        timer = null;
      },
    };
    pendingNotifications.add(notification);
    timer = scheduleTimer(() => {
      timer = null;
      runTrackedNotification(notification);
    }, 0);
  });
}

export async function flushTestQueryNotifications(): Promise<void> {
  let drainedNotifications = 0;

  for (let stabilityTurn = 0; stabilityTurn < MAX_STABILITY_TURNS; stabilityTurn += 1) {
    while (pendingNotifications.size > 0) {
      const notifications = [...pendingNotifications];
      for (const notification of notifications) {
        drainedNotifications += 1;
        if (drainedNotifications > MAX_DRAINED_NOTIFICATIONS) {
          cancelPendingNotifications();
          const boundError = new Error(
            `Query notification barrier exceeded ${MAX_DRAINED_NOTIFICATIONS} callbacks`,
          );
          const callbackErrors = pendingErrors.splice(0).map(({ value }) => value);
          if (callbackErrors.length > 0) {
            throw new AggregateError(
              [...callbackErrors, boundError],
              "Query notification barrier failed while draining callbacks",
            );
          }
          throw boundError;
        }

        notification.cancelTimer();
        runTrackedNotification(notification);
      }
    }

    const generationAtCheckpoint = schedulingGeneration;
    await waitForNextEventLoopTurn();

    if (pendingNotifications.size !== 0 || schedulingGeneration !== generationAtCheckpoint) {
      continue;
    }

    throwPendingErrors();
    return;
  }

  cancelPendingNotifications();
  const boundError = new Error(
    `Query notification barrier did not stabilize after ${MAX_STABILITY_TURNS} event-loop turns`,
  );
  const callbackErrors = pendingErrors.splice(0).map(({ value }) => value);
  if (callbackErrors.length > 0) {
    throw new AggregateError(
      [...callbackErrors, boundError],
      "Query notification barrier failed before reaching stability",
    );
  }
  throw boundError;
}
