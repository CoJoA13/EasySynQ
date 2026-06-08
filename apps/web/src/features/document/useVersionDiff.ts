import { useQuery } from "@tanstack/react-query";
import { useApi } from "../../lib/api";
import type { VersionDiff } from "../../lib/types";

// S-web-4: the doc 05 §8 redline of `fromVid` → `toVid` (two versions of the same document) —
// metadata diff + the inline text redline. Gated document.read_draft server-side: a reader without
// it gets a 403 (ApiError), which the viewer renders as quiet "no access" (DP-6). Enabled only when
// a distinct version pair is selected (the page's `?from=&to=` URL state).
export function useVersionDiff(
  documentId: string | null,
  toVid: string | null,
  fromVid: string | null,
) {
  const api = useApi();
  return useQuery({
    queryKey: ["version-diff", documentId, toVid, fromVid],
    queryFn: () =>
      api.get<VersionDiff>(
        `/api/v1/documents/${documentId}/versions/${toVid}/diff?from=${fromVid}`,
      ),
    enabled: documentId !== null && toVid !== null && fromVid !== null && toVid !== fromVid,
  });
}
