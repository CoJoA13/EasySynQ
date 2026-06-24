// apps/web/src/features/notifications/useNotificationStream.test.tsx
import { QueryClient } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders } from "../../test/render";
import { useNotificationStream } from "./hooks";

type OpenImpl = (token: string, onNudge: () => void, signal: AbortSignal) => Promise<void>;

function Harness({ openImpl }: { openImpl: OpenImpl }) {
  useNotificationStream(openImpl);
  return null;
}

describe("useNotificationStream", () => {
  afterEach(() => vi.restoreAllMocks());

  it("invalidates ['notifications'] on a notify nudge", async () => {
    const spy = vi.spyOn(QueryClient.prototype, "invalidateQueries");
    const open: OpenImpl = (_t, onNudge) => {
      onNudge();
      return new Promise(() => {}); // stay open (don't loop) after the nudge
    };
    renderWithProviders(<Harness openImpl={open} />);
    await vi.waitFor(() =>
      expect(spy).toHaveBeenCalledWith({ queryKey: ["notifications"] }),
    );
  });

  describe("with fake timers", () => {
    beforeEach(() => vi.useFakeTimers());
    afterEach(() => vi.useRealTimers());

    it("does not reconnect faster than the floor on accept-then-close", async () => {
      let calls = 0;
      const open: OpenImpl = () => {
        calls++;
        return Promise.resolve(); // accept then immediately close
      };
      renderWithProviders(<Harness openImpl={open} />);
      await vi.advanceTimersByTimeAsync(0);
      expect(calls).toBe(1);
      await vi.advanceTimersByTimeAsync(2_000); // < MIN_RECONNECT_MS floor (3_000)
      expect(calls).toBe(1);
      await vi.advanceTimersByTimeAsync(4_001); // crosses the backoff (3_000 doubled = 6_000, ≥ floor)
      expect(calls).toBe(2);
    });

    it("stops on unmount during the backoff window (no zombie reconnect)", async () => {
      let calls = 0;
      const open: OpenImpl = () => {
        calls++;
        return Promise.resolve();
      };
      const { unmount } = renderWithProviders(<Harness openImpl={open} />);
      await vi.advanceTimersByTimeAsync(0);
      expect(calls).toBe(1);
      unmount(); // abort during the backoff sleep
      await vi.advanceTimersByTimeAsync(60_000);
      expect(calls).toBe(1);
    });
  });
});
