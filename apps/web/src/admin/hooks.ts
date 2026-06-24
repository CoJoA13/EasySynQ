import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, useApi } from "../lib/api";
import type { NotificationDeliveryHealth, OrgConfig, OrgConfigUpdate } from "../lib/types";

// S-notify-5b: the admin Config tab consumes the existing GET/PATCH /admin/config (config.update-gated)
// + the new GET /admin/notifications/health. The config read derives a `forbidden` flag (the data-403 is
// the page's no-access boundary — NOT a usePermissions probe, which would flash on a cold /admin cache).
// refetchOnWindowFocus/Reconnect off so a focus refetch can't clobber unsaved toggle edits (#273).
export function useOrgConfig() {
  const api = useApi();
  const query = useQuery({
    queryKey: ["admin-config"],
    queryFn: () => api.get<OrgConfig>("/api/v1/admin/config"),
    retry: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });
  const forbidden = query.error instanceof ApiError && query.error.status === 403;
  return { ...query, forbidden };
}

export function useUpdateOrgConfig() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: OrgConfigUpdate) =>
      api.send<OrgConfig>("PATCH", "/api/v1/admin/config", body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["admin-config"] });
      // Flipping email-on should refresh the health banner.
      void qc.invalidateQueries({ queryKey: ["notification-health"] });
    },
  });
}

export function useNotificationHealth() {
  const api = useApi();
  const query = useQuery({
    queryKey: ["notification-health"],
    queryFn: () => api.get<NotificationDeliveryHealth>("/api/v1/admin/notifications/health"),
    retry: false,
    refetchOnWindowFocus: false,
  });
  const forbidden = query.error instanceof ApiError && query.error.status === 403;
  return { ...query, forbidden };
}
