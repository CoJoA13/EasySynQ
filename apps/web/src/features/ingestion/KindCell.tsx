import { Badge, Button, Group, Menu, Text } from "@mantine/core";
import type {
  ConfirmedKind,
  ImportClassification,
  ImportFileReview,
  ImportKind,
} from "../../lib/types";

// R10: the kind is an ALWAYS-HUMAN confirm. While the folded review.kind is "UNCONFIRMED" we render the
// engine's guess DIMMED with a trailing "?" (derived from the immutable classification.kind) plus a
// Confirm affordance — a small Menu so the human can override the guess (Document vs Record). The
// confirmed kind lives only on review.kind (never written back to classification); once it is
// DOCUMENT|RECORD we render a solid badge with no "?" and no Confirm. Bulk-accept does NOT route here —
// kind-confirm is a separate act (D-5). `busy` disables the affordance during an in-flight confirm.

const CONFIRMED_META: Record<ConfirmedKind, { label: string; mark: string; color: string }> = {
  DOCUMENT: { label: "Document", mark: "📄", color: "var(--es-info)" },
  RECORD: { label: "Record", mark: "🔒", color: "var(--es-success)" },
};

// The dimmed engine guess text for the UNCONFIRMED state. Records carry the WORM lock glyph; an UNKNOWN
// or absent classification reads "Unknown" (no "?", since there is no guess to confirm against).
function guessLabel(kind: ImportKind | undefined): string {
  if (kind === "DOCUMENT") return "Document?";
  if (kind === "RECORD") return "🔒 Record?";
  return "Unknown";
}

export function KindCell({
  review,
  classification,
  onConfirm,
  busy = false,
}: {
  review: ImportFileReview | null;
  classification: ImportClassification | null;
  onConfirm: (kind: ConfirmedKind) => void;
  busy?: boolean;
}) {
  const kind = review?.kind;

  // Confirmed (DOCUMENT|RECORD) → a solid badge, no "?", no Confirm.
  if (kind === "DOCUMENT" || kind === "RECORD") {
    const meta = CONFIRMED_META[kind];
    return (
      <Badge
        variant="filled"
        color={meta.color}
        size="sm"
        leftSection={<span aria-hidden="true">{meta.mark}</span>}
        aria-label={`Kind: ${meta.label}`}
      >
        {meta.label}
      </Badge>
    );
  }

  // UNCONFIRMED (or a null review) → the dimmed engine guess + a Confirm menu (Document / Record).
  return (
    <Group gap="xs" wrap="nowrap">
      <Text size="sm" c="dimmed" span aria-label={`Engine guess: ${guessLabel(classification?.kind)}`}>
        {guessLabel(classification?.kind)}
      </Text>
      <Menu position="bottom-start" withinPortal>
        <Menu.Target>
          <Button
            variant="subtle"
            size="compact-xs"
            disabled={busy}
            aria-label="Confirm kind"
            rightSection={<span aria-hidden="true">▾</span>}
          >
            Confirm
          </Button>
        </Menu.Target>
        <Menu.Dropdown>
          <Menu.Item onClick={() => onConfirm("DOCUMENT")}>Document</Menu.Item>
          <Menu.Item onClick={() => onConfirm("RECORD")}>Record</Menu.Item>
        </Menu.Dropdown>
      </Menu>
    </Group>
  );
}
