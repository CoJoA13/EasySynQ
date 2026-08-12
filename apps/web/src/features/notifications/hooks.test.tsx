import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import { AuthContext } from "../../lib/auth";
import { server } from "../../test/msw/server";
import { TEST_AUTH } from "../../test/render";
import { useNotificationCount, useNotificationPreferences, useNotifications } from "./hooks";
import { useMarkAllRead, useMarkRead, useUpdateNotificationPreferences } from "./mutations";

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <QueryClientProvider client={client}>
      <AuthContext.Provider value={TEST_AUTH}>{children}</AuthContext.Provider>
    </QueryClientProvider>
  );
}

describe("notification data layer", () => {
  it("useNotificationCount counts the unread fixtures", async () => {
    const { result } = renderHook(() => useNotificationCount(), { wrapper });
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.count).toBe(1); // one unread fixture
    expect(result.current.isError).toBe(false);
  });

  it("useNotificationCount reports isError and never a confident 0 on failure", async () => {
    server.use(http.get("/api/v1/notifications", () => new HttpResponse(null, { status: 500 })));
    const { result } = renderHook(() => useNotificationCount(), { wrapper });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.count).toBe(0); // placeholder consumed only behind the isError guard
  });

  it("useNotifications('all') returns the full list", async () => {
    const { result } = renderHook(() => useNotifications("all"), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toHaveLength(2);
  });

  it("useNotifications('recent') is disabled until enabled", async () => {
    const { result } = renderHook(() => useNotifications("recent", false), { wrapper });
    expect(result.current.fetchStatus).toBe("idle");
  });

  it("useNotificationPreferences reads the master toggle", async () => {
    const { result } = renderHook(() => useNotificationPreferences(), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.email_enabled).toBe(true);
  });

  it("useMarkRead POSTs the id", async () => {
    let marked = "";
    server.use(
      http.post("/api/v1/notifications/:id/read", ({ params }) => {
        marked = String(params.id);
        return HttpResponse.json({ status: "ok" });
      }),
    );
    const { result } = renderHook(() => useMarkRead(), { wrapper });
    result.current.mutate("abc-123");
    await waitFor(() => expect(marked).toBe("abc-123"));
  });

  it("useMarkRead delivers the error and exact id after its observer unmounts", async () => {
    let requestedId = "";
    let finishRequest: ((response: Response) => void) | undefined;
    const onError = vi.fn();
    server.use(
      http.post("/api/v1/notifications/:id/read", ({ params }) => {
        requestedId = String(params.id);
        return new Promise<Response>((resolve) => {
          finishRequest = resolve;
        });
      }),
    );

    const { result, unmount } = renderHook(() => useMarkRead({ onError }), { wrapper });
    act(() => result.current.mutate("abc-123"));
    await waitFor(() => expect(requestedId).toBe("abc-123"));

    unmount();
    await act(async () => {
      finishRequest?.(HttpResponse.json({ detail: "temporarily unavailable" }, { status: 503 }));
    });

    await waitFor(() => expect(onError).toHaveBeenCalledTimes(1));
    const [error, id] = onError.mock.calls[0] as [unknown, unknown];
    expect(error).toMatchObject({ status: 503 });
    expect(id).toBe("abc-123");
  });

  it("useMarkAllRead POSTs read-all", async () => {
    let hit = false;
    server.use(
      http.post("/api/v1/notifications/read-all", () => {
        hit = true;
        return HttpResponse.json({ marked: 3 });
      }),
    );
    const { result } = renderHook(() => useMarkAllRead(), { wrapper });
    result.current.mutate();
    await waitFor(() => expect(hit).toBe(true));
  });

  it("useUpdateNotificationPreferences PUTs a partial body", async () => {
    let body: unknown = null;
    server.use(
      http.put("/api/v1/me/notification-preferences", async ({ request }) => {
        body = await request.json();
        return HttpResponse.json(body as Record<string, unknown>);
      }),
    );
    const { result } = renderHook(() => useUpdateNotificationPreferences(), { wrapper });
    result.current.mutate({ digest_modes: { awareness: "off" } });
    await waitFor(() => expect(body).toEqual({ digest_modes: { awareness: "off" } }));
  });
});
