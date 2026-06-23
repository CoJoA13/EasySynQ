// apps/web/src/features/notifications/mutations.ts
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useApi } from "../../lib/api";
import type { NotificationPreferences, NotificationPreferencesUpdate } from "../../lib/types";

// Mark one read. Self-scoped; a 404 (foreign/already-gone id) is fire-and-forget — `.mutate()` does not
// throw to the caller and we navigate regardless; the 60 s poll backstops. onSuccess invalidates the
// ["notifications"] prefix → the badge + every list refresh together.
export function useMarkRead() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      api.send<{ status: string }>("POST", `/api/v1/notifications/${id}/read`),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["notifications"] }),
  });
}

export function useMarkAllRead() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.send<{ marked: number }>("POST", "/api/v1/notifications/read-all"),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["notifications"] }),
  });
}

export function useUpdateNotificationPreferences() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: NotificationPreferencesUpdate) =>
      api.send<NotificationPreferences>("PUT", "/api/v1/me/notification-preferences", body),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["notification-preferences"] }),
  });
}
