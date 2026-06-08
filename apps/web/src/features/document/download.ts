import { useState } from "react";
import { useApi } from "../../lib/api";
import { useAuth } from "../../lib/auth";
import type { DocumentDownload } from "../../lib/types";

// Shared controlled-copy download (the drawer Overview tab + the page's RenditionCard). Fetches the
// presigned GET on demand and opens it in a new tab — the api never proxies bytes (D1); the presign
// is browser-reachable (#90). A transient presign failure is non-fatal for a read-only view (quiet).
// The rendition flag (controlled_copy vs source) is surfaced after the fetch.
export function useControlledCopyDownload(documentId: string) {
  const api = useApi();
  const [downloading, setDownloading] = useState(false);
  const [rendition, setRendition] = useState<DocumentDownload["rendition"] | null>(null);

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

// S-web-4: export a fresh per-request stamped controlled-copy PDF (GET /documents/{id}/export, gate
// document.export). Unlike /download this is an AUTHENTICATED stream (not a presign), so it needs the
// bearer — fetch → blob → object URL → open. Held by no seeded role, so the button is quiet-absent by
// default (the caller gates on can("document.export")). A transient failure is non-fatal (quiet).
export function useDocumentExport(documentId: string) {
  const { token } = useAuth();
  const [exporting, setExporting] = useState(false);

  async function exportCopy() {
    setExporting(true);
    try {
      const resp = await fetch(`/api/v1/documents/${documentId}/export`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      if (!resp.ok) return;
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      window.open(url, "_blank", "noopener,noreferrer");
      setTimeout(() => URL.revokeObjectURL(url), 60_000);
    } catch {
      /* quiet — a transient export failure is non-fatal for a read-only view */
    } finally {
      setExporting(false);
    }
  }

  return { exportCopy, exporting };
}
