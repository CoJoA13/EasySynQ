import { Button, Card, Group, Stack, Text } from "@mantine/core";
import type { ImportChecklist, ImportChecklistBlocker } from "../../lib/types";

// A human label for each known blocker `type`; an unknown type degrades to a title-cased fallback so
// a future backend blocker type never renders a raw enum token (the "tolerate additive" rule).
const BLOCKER_LABELS: Record<string, string> = {
  duplicate_identifier_within_import: "Duplicate-identifier conflicts",
  collides_with_vault_doc: "Collides with an existing vault document",
  singleton_type_already_effective: "Singleton type already Effective",
  ambiguous_unresolved: "Ambiguous classification unresolved",
};

function blockerLabel(type: string): string {
  return (
    BLOCKER_LABELS[type] ??
    type.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase())
  );
}

// One danger row per blocking[] entry: a ✕ glyph + the human label + a "Show items" button that
// bubbles the blocker up (ReviewCockpit filters the table to the offenders — this leaf is purely
// presentational). DP-7: the glyph carries the meaning, color is the redundant third channel.
function BlockerRow({
  blocker,
  onShowBlocker,
}: {
  blocker: ImportChecklistBlocker;
  onShowBlocker: (b: ImportChecklistBlocker) => void;
}) {
  const label = blockerLabel(blocker.type);
  // The offending files inline — the identifier (when present) + a guarded count — so the reviewer can
  // identify the conflicting files even for an already-accepted conflict (the filter-jump only helps
  // the undecided case). file_ids may be absent for a type that carries no file list.
  const fileCount = blocker.file_ids?.length;
  const detailBits = [
    blocker.identifier ? `“${blocker.identifier}”` : null,
    fileCount ? `${fileCount} file${fileCount === 1 ? "" : "s"}` : null,
  ].filter(Boolean) as string[];
  return (
    <Group
      justify="space-between"
      wrap="nowrap"
      py={6}
      aria-label={`Blocking: ${label}`}
      style={{ borderBottom: "1px solid var(--es-border)" }}
    >
      <Group gap="xs" wrap="nowrap">
        <Text span aria-hidden="true" c="var(--es-danger)" fw={700}>
          ✕
        </Text>
        <Text span size="sm">
          {label}
        </Text>
        {detailBits.length > 0 && (
          <Text span size="sm" c="dimmed">
            {detailBits.join(" · ")}
          </Text>
        )}
      </Group>
      <Button variant="light" color="var(--es-danger)" size="compact-sm" onClick={() => onShowBlocker(blocker)}>
        Show items
      </Button>
    </Group>
  );
}

// A non-blocking advisory row: a leading glyph + a label + a right-aligned value caption. Never
// danger — these are completeness signals, not commit blocks (D-3 / R10). `tone` picks the calm
// glyph/color; the value is always pre-formatted + undefined-guarded by the caller.
function AdvisoryRow({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone: "ok" | "warn" | "neutral";
}) {
  const glyph = tone === "ok" ? "✓" : tone === "warn" ? "▲" : "•";
  const color =
    tone === "ok" ? "var(--es-success)" : tone === "warn" ? "var(--es-warning)" : "var(--es-text-muted)";
  return (
    <Group
      justify="space-between"
      wrap="nowrap"
      py={6}
      aria-label={`Advisory: ${label}`}
      style={{ borderBottom: "1px solid var(--es-border)" }}
    >
      <Group gap="xs" wrap="nowrap">
        <Text span aria-hidden="true" c={color} fw={700}>
          {glyph}
        </Text>
        <Text span size="sm">
          {label}
        </Text>
      </Group>
      <Text span size="sm" fw={600} c="dimmed">
        {value}
      </Text>
    </Group>
  );
}

export function PreCommitChecklist({
  checklist,
  onShowBlocker,
}: {
  checklist: ImportChecklist;
  onShowBlocker: (blocker: ImportChecklistBlocker) => void;
}) {
  const blocking = checklist.blocking ?? [];
  const review = checklist.review;
  const advisory = checklist.advisory ?? {};

  // ★ coverage — guard the whole sub-object AND each field (additionalProperties:true → may be absent).
  const cov = advisory.star_coverage ?? undefined;
  const covSatisfied = cov?.satisfied ?? undefined;
  const covTotal = cov?.total ?? undefined;
  const covValue = `${covSatisfied ?? "—"} / ${covTotal ?? "—"} satisfied`;

  // kind-confirmed — warn while any item is still unconfirmed (advisory, never a hard block).
  const kindConfirmed = review?.kind_confirmed ?? 0;
  const keepItems = review?.keep_items ?? 0;
  const kindIncomplete = kindConfirmed < keepItems;

  const unknownLow = advisory.unknown_low ?? 0;

  return (
    <Card withBorder padding="md" radius="md">
      <Stack gap={2} mb="sm">
        <Text fw={600}>Pre-commit checklist</Text>
        <Text size="sm" c="dimmed">
          A calm gate before anything becomes controlled — advisory, never an auto-compliance judgment.
        </Text>
      </Stack>

      <Stack gap={0}>
        {blocking.map((b, i) => (
          <BlockerRow key={`${b.type}-${i}`} blocker={b} onShowBlocker={onShowBlocker} />
        ))}
        <AdvisoryRow
          label="Kind confirmed on every item"
          value={`${kindConfirmed} / ${keepItems}`}
          tone={kindIncomplete ? "warn" : "ok"}
        />
        <AdvisoryRow label="Mandatory ISO clause coverage" value={covValue} tone="neutral" />
        <AdvisoryRow label="Unknown / Low triaged" value={String(unknownLow)} tone="neutral" />
      </Stack>

      <Stack gap={2} mt="sm">
        <Text size="sm" c="dimmed" maw="70ch">
          Mandatory-coverage is a non-blocking projection of the Compliance Checklist onto the confirmed
          set — missing items may simply not exist yet.
        </Text>
        <Text size="sm" c="dimmed">
          Commit can proceed with gaps.
        </Text>
      </Stack>
    </Card>
  );
}
