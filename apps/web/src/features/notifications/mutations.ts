// apps/web/src/features/notifications/mutations.ts
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useApi } from "../../lib/api";
import { useMutationFeedback } from "../../lib/mutationFeedback";
import type { NotificationPreferences, NotificationPreferencesUpdate } from "../../lib/types";

type NotificationWriteApi = ReturnType<typeof useApi>;

export function markNotificationRead(api: NotificationWriteApi, notificationId: string) {
  return api.send<{ status: string }>("POST", `/api/v1/notifications/${notificationId}/read`);
}

// Mark one read. The request and prefix invalidation are shared by explicit row actions and link-open
// actions; callers choose whether the failure stays local or is retained across navigation.
export function useMarkRead(options?: { onError?: (error: unknown, id: string) => void }) {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => markNotificationRead(api, id),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["notifications"] }),
    onError: options?.onError,
  });
}

export function useMarkReadOnOpen(notificationTitle: string) {
  const api = useApi();
  const qc = useQueryClient();
  const feedback = useMutationFeedback();

  return useMarkRead({
    onError: (error, id) => {
      feedback.report({
        key: `mark-read:${id}`,
        title: `This notification remains unread: ${notificationTitle}`,
        error,
        retry: async () => {
          await markNotificationRead(api, id);
          await qc.invalidateQueries({ queryKey: ["notifications"] });
        },
        retryLabel: `Try marking ${notificationTitle} read again`,
        dismissLabel: `Dismiss mark-read error for ${notificationTitle}`,
        successMessage: "Notification marked read",
      });
    },
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
