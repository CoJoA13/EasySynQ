import { Alert, Anchor, Card, Group, Loader, Stack, Table, Text } from "@mantine/core";
import { useMemo, useRef, useState } from "react";
import { ApiError, useApi } from "../../lib/api";
import type { MetadataDiffEntry } from "../../lib/types";
import { useVersionDiff } from "./useVersionDiff";

// Render an arbitrary metadata value compactly; empty/null → em-dash.
function fmt(v: unknown): string {
  if (v === null || v === undefined || v === "") return "—";
  return typeof v === "string" ? v : JSON.stringify(v);
}

// S-web-4 (doc 11 §4.7): the Text-redline + Metadata-diff viewer for two immutable versions of one
// document. The visual page-image (side-by-side) mode is S-web-4b. Read-only; gated
// document.read_draft (403 → quiet, DP-6). Status is NEVER color-only — insert/delete carry +/-
// markers + <ins>/<del> semantics (DP-7 / WCAG 2.2). n/p navigate the changes; a change index
// lists them for screen-reader users.
export function RedlineViewer({
  documentId,
  fromVid,
  toVid,
}: {
  documentId: string;
  fromVid: string;
  toVid: string;
}) {
  const api = useApi();
  const { data, isLoading, isError, error } = useVersionDiff(documentId, toVid, fromVid);
  const changeRefs = useRef<(HTMLElement | null)[]>([]);
  const [active, setActive] = useState(-1);

  // The changed text hunks (insert/delete), in document order — the nav targets + the SR index.
  const changes = useMemo(
    () => (data?.text_diff.hunks ?? []).filter((h) => h.op !== "equal"),
    [data],
  );

  if (isLoading) return <Loader size="sm" aria-label="Loading redline" />;
  if (isError) {
    if (error instanceof ApiError && error.status === 403) {
      return (
        <Text size="sm" c="dimmed">
          You don't have access to the redline.
        </Text>
      );
    }
    return (
      <Text size="sm" c="red">
        Could not load the redline.
      </Text>
    );
  }
  if (!data) return null;

  function focusChange(idx: number) {
    if (changes.length === 0) return;
    const clamped = Math.max(0, Math.min(idx, changes.length - 1));
    setActive(clamped);
    const el = changeRefs.current[clamped];
    el?.focus();
    el?.scrollIntoView?.({ block: "center", behavior: "auto" });
  }

  function onKeyDown(e: React.KeyboardEvent) {
    if (e.key === "n") {
      e.preventDefault();
      focusChange(active + 1);
    } else if (e.key === "p") {
      e.preventDefault();
      focusChange(active - 1);
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

  const changedMeta = (data.metadata_diff ?? []).filter((m) => m.changed);

  // Pre-build the hunk elements; changed hunks get a focusable ref in change-order.
  let ci = 0;
  const hunkEls = (data.text_diff.hunks ?? []).map((h, i) => {
    if (h.op === "equal") {
      return (
        <div
          key={i}
          style={{
            whiteSpace: "pre-wrap",
            fontSize: "0.875rem",
            color: "var(--es-text-muted)",
            padding: "2px 6px",
          }}
        >
          {h.text}
        </div>
      );
    }
    const idx = ci++;
    const isIns = h.op === "insert";
    const common = {
      tabIndex: -1,
      "aria-label": `${isIns ? "Added" : "Removed"}: ${h.text}`,
      ref: (el: HTMLElement | null) => {
        changeRefs.current[idx] = el;
      },
      style: {
        display: "block",
        whiteSpace: "pre-wrap" as const,
        fontSize: "0.875rem",
        textDecoration: isIns ? "underline" : "line-through",
        color: isIns ? "var(--es-success-text)" : "var(--es-danger-text)",
        background: isIns ? "var(--es-success-soft)" : "var(--es-danger-soft)",
        padding: "2px 6px",
        borderRadius: 4,
      },
    };
    return isIns ? (
      <ins key={i} {...common}>
        + {h.text}
      </ins>
    ) : (
      <del key={i} {...common}>
        − {h.text}
      </del>
    );
  });

  return (
    <Card withBorder>
      <Stack gap="md">
        <div>
          <Text fw={600}>
            {data.from.revision_label} → {data.to.revision_label}
          </Text>
          {data.to.change_reason && (
            <Text size="sm" c="dimmed">
              {data.to.change_reason}
            </Text>
          )}
        </div>

        {/* Metadata diff */}
        <Stack gap={4}>
          <Text size="xs" fw={700} c="dimmed" tt="uppercase">
            Control-metadata changes
          </Text>
          {changedMeta.length === 0 ? (
            <Text size="sm" c="dimmed">
              No control-metadata changes.
            </Text>
          ) : (
            <Table withRowBorders={false} aria-label="Control-metadata changes">
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Field</Table.Th>
                  <Table.Th>From</Table.Th>
                  <Table.Th>To</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {changedMeta.map((m: MetadataDiffEntry) => (
                  <Table.Tr key={m.field}>
                    <Table.Td>
                      <Text size="sm">{m.field}</Text>
                    </Table.Td>
                    <Table.Td>
                      <Text size="sm" c="dimmed">
                        {fmt(m.from)}
                      </Text>
                    </Table.Td>
                    <Table.Td>
                      <Text size="sm">{fmt(m.to)}</Text>
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          )}
        </Stack>

        {/* Text redline */}
        <Stack gap={4}>
          <Group justify="space-between" align="center">
            <Text size="xs" fw={700} c="dimmed" tt="uppercase">
              Text redline
            </Text>
            {data.text_diff.status === "ok" && changes.length > 0 && (
              <Text size="xs" c="dimmed">
                {changes.length} change{changes.length === 1 ? "" : "s"} · press n / p to navigate
              </Text>
            )}
          </Group>

          {data.text_diff.status === "unavailable" ? (
            <Alert color="yellow" title="Text redline unavailable">
              <Stack gap="xs">
                <Text size="sm">
                  {data.text_diff.reason ??
                    "The text could not be extracted for an inline redline."}
                </Text>
                <Group gap="md">
                  <Anchor component="button" type="button" onClick={() => void openSource(fromVid)}>
                    Download {data.from.revision_label} source
                  </Anchor>
                  <Anchor component="button" type="button" onClick={() => void openSource(toVid)}>
                    Download {data.to.revision_label} source
                  </Anchor>
                </Group>
              </Stack>
            </Alert>
          ) : changes.length === 0 ? (
            <Text size="sm" c="dimmed">
              No text changes between these versions.
            </Text>
          ) : (
            <>
              <nav aria-label="Changes">
                <Stack gap={2}>
                  {changes.map((h, idx) => (
                    <Anchor
                      key={idx}
                      component="button"
                      type="button"
                      size="sm"
                      ta="left"
                      onClick={() => focusChange(idx)}
                    >
                      {h.op === "insert" ? "Added" : "Removed"}: {h.text.slice(0, 60)}
                    </Anchor>
                  ))}
                </Stack>
              </nav>
              <Stack
                gap={2}
                role="group"
                aria-label={`Text redline ${data.from.revision_label} to ${data.to.revision_label}`}
                tabIndex={0}
                onKeyDown={onKeyDown}
                style={{ outline: "none" }}
              >
                {hunkEls}
              </Stack>
            </>
          )}
        </Stack>
      </Stack>
    </Card>
  );
}
