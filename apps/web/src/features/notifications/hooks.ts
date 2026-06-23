// apps/web/src/features/notifications/hooks.ts
import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import { useApi } from "../../lib/api";
import type { Notification, NotificationPreferences } from "../../lib/types";

// The unread-count badge — the ONLY polled query (60 s). Mirrors useAckCount EXACTLY: it returns the
// count ALONGSIDE isError/isLoading, so the bell reads `count` only behind the isError guard and renders
// an indeterminate state on failure — NEVER a confident 0 (the silent-zero fix). limit=100 caps the
// fetch; the bell shows "99+" when count > 99.
export function useNotificationCount(): { count: number; isError: boolean; isLoading: boolean } {
  const api = useApi();
  const query = useQuery({
    queryKey: ["notifications", "count"],
    queryFn: () => api.get<Notification[]>("/api/v1/notifications?unread_only=true&limit=100"),
    refetchInterval: 60_000,
    retry: false,
  });
  return { count: query.data?.length ?? 0, isError: query.isError, isLoading: query.isLoading };
}

// The center list. "recent" → the popover (15, read+unread); "all" → the page (50, read+unread).
// `enabled` gates the popover fetch on the popover being open.
export function useNotifications(
  scope: "recent" | "all",
  enabled = true,
): UseQueryResult<Notification[]> {
  const api = useApi();
  const limit = scope === "recent" ? 15 : 50;
  return useQuery({
    queryKey: ["notifications", "list", scope],
    queryFn: () => api.get<Notification[]>(`/api/v1/notifications?limit=${limit}`),
    enabled,
    retry: false,
  });
}

export function useNotificationPreferences(): UseQueryResult<NotificationPreferences> {
  const api = useApi();
  return useQuery({
    queryKey: ["notification-preferences"],
    queryFn: () => api.get<NotificationPreferences>("/api/v1/me/notification-preferences"),
    retry: false,
    // The settings page seeds a local working copy from this query and diffs it for "dirty". The app's
    // default QueryClient (main.tsx) leaves refetchOnWindowFocus/Reconnect on, so a focus/reconnect
    // refetch that returns changed data would re-seed and clobber unsaved edits. Disable those here; the
    // post-save invalidate still refetches to reset the form (Codex #273 P2).
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });
}
