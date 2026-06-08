import {
  Alert,
  Anchor,
  Badge,
  Card,
  Group,
  Loader,
  SegmentedControl,
  Skeleton,
  Stack,
  Text,
  UnstyledButton,
  VisuallyHidden,
} from "@mantine/core";
import { useEffect, useRef, useState } from "react";
import { ApiError, useApi } from "../../lib/api";
import type { VisualDiffLayer } from "../../lib/types";
import { useVisualDiff } from "./useVisualDiff";

const LAYERS: { value: VisualDiffLayer; label: string }[] = [
  { value: "from", label: "Before" },
  { value: "to", label: "After" },
  { value: "diff", label: "Diff" },
];

// Fetch one page+layer PNG through the AUTHENTICATED page endpoint (the bearer can't ride a bare
// <img src>), turn it into an objectURL, and revoke the prior one on every change + on unmount. A
// 404 means "no image on this side for this page" (an added page's Before, a removed page's After).
type ImgState = "loading" | "ready" | "missing" | "error";
function usePageImage(
  api: ReturnType<typeof useApi>,
  documentId: string,
  toVid: string,
  fromVid: string,
  page: number,
  layer: VisualDiffLayer,
  enabled: boolean,
) {
  const [src, setSrc] = useState<string | null>(null);
  const [state, setState] = useState<ImgState>("loading");

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    let objectUrl: string | null = null;
    setState("loading");
    setSrc(null);
    const url = `/api/v1/documents/${documentId}/versions/${toVid}/visual-diff/page/${page}?from=${fromVid}&layer=${layer}`;
    api
      .getBlob(url)
      .then((blob) => {
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setSrc(objectUrl);
        setState("ready");
      })
      .catch((e) => {
        if (cancelled) return;
        setState(e instanceof ApiError && e.status === 404 ? "missing" : "error");
      });
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [api, documentId, toVid, fromVid, page, layer, enabled]);

  return { src, state };
}

// S-web-4b (doc 11 §4.7 visual mode): the worker-async page-image diff for two immutable versions.
// A single image pane + a Before/After/Diff layer toggle (default Diff = the server-composed overlay),
// a changed-page rail (the §4.7 minimap AND the §6.2 screen-reader change index, marked non-color),
// and n/p next/previous-changed-page nav. Read-only, gated document.read_draft (403 → quiet, DP-6).
// The async render reads as a phased long-op (§4.9), never a frozen UI; Failed → Retry, Unavailable →
// a calm source-download fallback.
export function VisualDiffViewer({
  documentId,
  fromVid,
  toVid,
}: {
  documentId: string;
  fromVid: string;
  toVid: string;
}) {
  const api = useApi();
  // Mounting IS the enable signal — the viewer is only rendered in visual mode.
  const { status, isLoading, isError, error, retry } = useVisualDiff(documentId, toVid, fromVid, true);

  const pages = status?.pages ?? [];
  const changed = pages.filter((p) => p.changed).map((p) => p.page);
  const firstPage = changed[0] ?? 0;
  const [picked, setPicked] = useState<number | null>(null);
  const [layer, setLayer] = useState<VisualDiffLayer>("diff");
  const page = picked ?? firstPage;
  const railRefs = useRef<(HTMLButtonElement | null)[]>([]);

  const isReady = status?.status === "Ready";
  const img = usePageImage(api, documentId, toVid, fromVid, page, layer, isReady);

  function gotoPage(p: number) {
    setPicked(p);
    railRefs.current[p]?.focus();
  }

  function onKeyDown(e: React.KeyboardEvent) {
    if (changed.length === 0) return;
    if (e.key === "n") {
      e.preventDefault();
      gotoPage(changed.find((p) => p > page) ?? changed[changed.length - 1]!);
    } else if (e.key === "p") {
      e.preventDefault();
      gotoPage([...changed].reverse().find((p) => p < page) ?? changed[0]!);
    }
  }

  async function openSource(vid: string) {
    try {
      const res = await api.get<{ download_url: string }>(
        `/api/v1/documents/${documentId}/versions/${vid}/download`,
      );
      window.open(res.download_url, "_blank", "noopener,noreferrer");
    } catch {
      /* quiet — a transient presign failure is non-fatal */
    }
  }

  // --- 403 → quiet (DP-6) ---
  if (isError && error instanceof ApiError && error.status === 403) {
    return (
      <Text size="sm" c="dimmed">
        You don't have access to the visual diff.
      </Text>
    );
  }
  if (isError) {
    return (
      <Text size="sm" c="red">
        Could not load the visual diff.
      </Text>
    );
  }

  // --- Pending / loading → the §4.9 phased long-op affordance (skeletons match the final layout) ---
  if (isLoading || status?.status === "Pending") {
    return (
      <Card withBorder>
        <Stack gap="sm">
          <Group gap="xs">
            <Loader size="sm" />
            <Text size="sm">Rendering page images…</Text>
          </Group>
          <Group align="flex-start" wrap="nowrap">
            <Stack gap={6} w={120}>
              <Skeleton h={20} />
              <Skeleton h={20} />
              <Skeleton h={20} />
            </Stack>
            <Skeleton h={320} style={{ flex: 1 }} />
          </Group>
          <Group justify="space-between">
            <Text size="xs" c="dimmed">
              This runs in the background — you can keep using the page.
            </Text>
            <Anchor component="button" type="button" size="sm" onClick={retry}>
              Re-request render
            </Anchor>
          </Group>
          <VisuallyHidden aria-live="polite">Rendering page images.</VisuallyHidden>
        </Stack>
      </Card>
    );
  }

  // --- Failed → scoped, recoverable banner (§4.9 error row) ---
  if (status?.status === "Failed") {
    return (
      <Alert color="red" title="Visual diff failed">
        <Stack gap="xs">
          <Text size="sm">{status.reason ?? "The page images could not be rendered."}</Text>
          <Anchor component="button" type="button" onClick={retry}>
            Retry
          </Anchor>
        </Stack>
      </Alert>
    );
  }

  // --- Unavailable → a non-renderable version; calm, with the source-download fallback ---
  if (status?.status === "Unavailable") {
    return (
      <Alert color="yellow" title="Visual diff unavailable">
        <Stack gap="xs">
          <Text size="sm">
            {status.reason ?? "One of these versions can't be rendered to page images."}{" "}
            The text redline still works; or open the source files:
          </Text>
          <Group gap="md">
            <Anchor component="button" type="button" onClick={() => void openSource(fromVid)}>
              Download Before source
            </Anchor>
            <Anchor component="button" type="button" onClick={() => void openSource(toVid)}>
              Download After source
            </Anchor>
          </Group>
        </Stack>
      </Alert>
    );
  }

  if (!isReady) return null;

  const pageCount = status?.page_count ?? pages.length;
  const isChanged = pages.find((p) => p.page === page)?.changed ?? false;
  const layerLabel = LAYERS.find((l) => l.value === layer)?.label ?? layer;

  return (
    <Card withBorder>
      <Stack gap="md" role="group" aria-label="Visual page diff" tabIndex={0} onKeyDown={onKeyDown} style={{ outline: "none" }}>
        <Group justify="space-between" align="center">
          <Text size="xs" fw={700} c="dimmed" tt="uppercase">
            Page images
          </Text>
          <SegmentedControl
            size="xs"
            aria-label="Image layer"
            value={layer}
            onChange={(v) => setLayer(v as VisualDiffLayer)}
            data={LAYERS}
          />
        </Group>

        <Group align="flex-start" wrap="nowrap" gap="md">
          {/* The changed-page rail = the §4.7 minimap + the §6.2 screen-reader change index. */}
          <nav aria-label="Pages" style={{ minWidth: 130 }}>
            <Stack gap={4}>
              {pages.map((p) => (
                <UnstyledButton
                  key={p.page}
                  ref={(el: HTMLButtonElement | null) => {
                    railRefs.current[p.page] = el;
                  }}
                  aria-current={p.page === page ? "true" : undefined}
                  aria-label={`Page ${p.page + 1}${p.changed ? ", changed" : ""}`}
                  onClick={() => gotoPage(p.page)}
                  style={{
                    padding: "4px 8px",
                    borderRadius: 4,
                    fontSize: "0.875rem",
                    fontWeight: p.page === page ? 700 : 400,
                    color: p.page === page ? "var(--es-accent, #2563eb)" : "var(--es-text-muted, #6b7280)",
                    background: p.page === page ? "var(--es-accent-soft, #eff6ff)" : "transparent",
                  }}
                >
                  <Group gap={6} wrap="nowrap">
                    <span>Page {p.page + 1}</span>
                    {p.changed && (
                      <Badge size="xs" variant="light" color="orange" aria-hidden>
                        ✱ changed
                      </Badge>
                    )}
                  </Group>
                </UnstyledButton>
              ))}
            </Stack>
          </nav>

          {/* The single image pane. */}
          <div style={{ flex: 1, minHeight: 200 }}>
            {img.state === "loading" && <Skeleton h={320} />}
            {img.state === "ready" && img.src && (
              <img
                src={img.src}
                alt={`Page ${page + 1} of ${pageCount} — ${layerLabel} layer (${isChanged ? "changed" : "unchanged"})`}
                style={{ maxWidth: "100%", height: "auto", border: "1px solid var(--es-border, #e5e7eb)" }}
              />
            )}
            {img.state === "missing" && (
              <Text size="sm" c="dimmed">
                No image on this side for this page.
              </Text>
            )}
            {img.state === "error" && (
              <Text size="sm" c="red">
                Could not load this page image.
              </Text>
            )}
          </div>
        </Group>

        <Group justify="space-between">
          <Text size="xs" c="dimmed">
            {changed.length > 0
              ? `${changed.length} of ${pageCount} page${pageCount === 1 ? "" : "s"} changed · press n / p to step changed pages`
              : "No page-image differences."}
          </Text>
          <Text size="xs" c="dimmed">
            Page-region differences — the footer/watermark band differs by revision.
          </Text>
        </Group>

        <VisuallyHidden aria-live="polite">
          Visual diff ready. Showing page {page + 1} of {pageCount}, {layerLabel} layer
          {isChanged ? ", changed" : ", unchanged"}.
        </VisuallyHidden>
      </Stack>
    </Card>
  );
}
