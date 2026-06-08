import { useQuery } from "@tanstack/react-query";
import { useApi } from "../../lib/api";

// The caller's own app_user identity (GET /me). `id` is the **app_user.id** — the value that appears
// in a task's candidate_pool / assignee_user_id — NOT the OIDC `sub` (lib/auth's keycloak subject is a
// DIFFERENT identifier). Any client-side task-membership check must compare against this `id`.
export interface Me {
  id: string;
  keycloak_subject: string;
  display_name: string | null;
  email: string | null;
  status: string;
}

export function useMe() {
  const api = useApi();
  return useQuery({ queryKey: ["me"], queryFn: () => api.get<Me>("/api/v1/me") });
}
