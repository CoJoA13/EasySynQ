import { useEffect, useState } from "react";
import { useApi } from "../../lib/api";
import type { DocumentDownload } from "../../lib/types";

// Shared controlled-copy download (the drawer Overview tab + the page's RenditionCard). Fetches the
// presigned GET on demand and opens it in a new tab — the api never proxies bytes (D1); the presign
// is browser-reachable (#90). A transient presign failure is non-fatal for a read-only view (quiet).
// The rendition flag (controlled_copy vs source) is surfaced after the fetch.
export function useControlledCopyDownload(documentId: string) {
  const api = useApi();
  const [downloading, setDownloading] = useState(false);
  const [rendition, setRendition] = useState<DocumentDownload["rendition"] | null>(null);

  // Reset the rendition note when the page navigates to a different document — the /documents/:id
  // route element is reused across a param-only change, so the local state would otherwise linger.
  useEffect(() => {
    setRendition(null);
  }, [documentId]);

  async function open() {
    setDownloading(true);
    try {
      const res = await api.get<DocumentDownload>(`/api/v1/documents/${documentId}/download`);
      setRendition(res.rendition ?? null);
      window.open(res.download_url, "_blank", "noopener,noreferrer");
    } catch {
      /* quiet — a transient presign failure is non-fatal for a read-only view */
    } finally {
      setDownloading(false);
    }
  }

  return { open, downloading, rendition };
}
