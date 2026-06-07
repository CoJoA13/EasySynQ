import { useQuery } from "@tanstack/react-query";
import { useApi } from "../../lib/api";
import type { DirectoryUser } from "../../lib/types";

// S-web-2: the minimal user-name directory, used to resolve owner_user_id → display name (the
// library Owner column + facet). Shell-scoped so the cached map is shared page ↔ drawer.
export function useUserDirectory() {
  const api = useApi();
  return useQuery({
    queryKey: ["directory-users"],
    queryFn: () => api.get<DirectoryUser[]>("/api/v1/directory/users"),
  });
}
