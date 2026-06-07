import { Badge, Group, Stack, Text, Title } from "@mantine/core";
import type { DocumentSummary } from "../../lib/types";
import { StateBadge } from "./StateBadge";

// The ONE artifact header (DP-5) — identifier · state · title · type · owner · effective date ·
// clause chips. Lens-agnostic + reused verbatim by the library detail drawer and (S-web-3) the
// full Document page. Friendly type/owner are resolved by the caller (passed in); a missing name
// degrades to "—" (DP-6 quiet absence), never a raw UUID in the primary surface.
function isoDate(iso: string | null): string {
  return iso ? iso.slice(0, 10) : "—";
}

export function ArtifactHeader({
  doc,
  typeName,
  ownerName,
}: {
  doc: DocumentSummary;
  typeName?: string;
  ownerName?: string;
}) {
  return (
    <Stack gap="xs">
      <Group gap="sm" align="center">
        <Text ff="monospace" fw={600} size="sm">
          {doc.identifier}
        </Text>
        <StateBadge state={doc.current_state} size="lg" />
      </Group>
      <Title order={3}>{doc.title}</Title>
      <Group gap="lg">
        <Text size="sm" c="dimmed">
          Type: {typeName ?? "—"}
        </Text>
        <Text size="sm" c="dimmed">
          Owner: {ownerName ?? "—"}
        </Text>
        {doc.effective_from && (
          <Text size="sm" c="dimmed">
            Effective since {isoDate(doc.effective_from)}
          </Text>
        )}
      </Group>
      {doc.clause_refs && doc.clause_refs.length > 0 && (
        <Group gap={4}>
          {doc.clause_refs.map((c) => (
            <Badge key={c} variant="outline" color="var(--es-accent)">
              {c}
            </Badge>
          ))}
        </Group>
      )}
    </Stack>
  );
}
