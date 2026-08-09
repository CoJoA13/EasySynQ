export const SETUP_STATE_TIMEOUT_MS = 15_000;

const SETUP_STATES = new Set(["UNINITIALIZED", "IN_SETUP", "OPERATIONAL"] as const);

export type SetupState = "UNINITIALIZED" | "IN_SETUP" | "OPERATIONAL";

export interface SetupStateResponse {
  setup_state: SetupState;
}

export function parseSetupState(value: unknown): SetupStateResponse {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error("invalid setup state response");
  }
  const setupState = (value as Record<string, unknown>).setup_state;
  if (typeof setupState !== "string" || !SETUP_STATES.has(setupState as SetupState)) {
    throw new Error("invalid setup state response");
  }
  return { setup_state: setupState as SetupState };
}

export async function fetchSetupState(signal?: AbortSignal): Promise<SetupStateResponse> {
  const controller = new AbortController();
  let timer: ReturnType<typeof setTimeout> | undefined;
  let rejectCancellation: ((reason: unknown) => void) | undefined;

  const cancelled = new Promise<never>((_resolve, reject) => {
    rejectCancellation = reject;
  });
  const cancel = () => {
    controller.abort();
    rejectCancellation?.(new DOMException("Setup state request cancelled", "AbortError"));
  };
  if (signal?.aborted) cancel();
  else signal?.addEventListener("abort", cancel, { once: true });

  const timeout = new Promise<never>((_resolve, reject) => {
    timer = setTimeout(() => {
      controller.abort();
      reject(new Error("setup state request timed out"));
    }, SETUP_STATE_TIMEOUT_MS);
  });

  const request = (async () => {
    const response = await fetch("/api/v1/setup/state", {
      signal: controller.signal,
    });
    if (!response.ok) throw new Error("setup state request failed");
    return parseSetupState(await response.json());
  })();

  try {
    return await Promise.race([request, timeout, cancelled]);
  } finally {
    if (timer !== undefined) clearTimeout(timer);
    signal?.removeEventListener("abort", cancel);
  }
}
