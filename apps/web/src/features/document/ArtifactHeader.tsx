import { Group, Stack, Text, Title } from "@mantine/core";
import { ClauseBadge } from "../../lib/ClauseBadge";
import type { DocumentSummary } from "../../lib/types";
import { StateBadge } from "./StateBadge";

// The ONE artifact header (DP-5) — identifier · state · title · type · owner · effective date ·
// clause chips. Lens-agnostic + reused verbatim by the library detail drawer and (S-web-3) the
// full Document page. Friendly type/owner are resolved by the caller (passed in); a missing name
// degrades to "—" (DP-6 quiet absence), never a raw UUID in the primary surface.
//
// `order` exists because those two render sites sit at genuinely different depths, and this is the
// only heading in the SPA where that is true. On /documents/:id this header IS the page — it holds
// the route's sole heading — so DocumentDetailPage passes 1. In the library drawer it sits under
// LibraryPage's own h1 AND under the h2 that Mantine's Drawer renders for its `title` prop
// (ModalBaseTitle is hard-coded `component: "h2"`), so the default of 3 is already correct there
// and the drawer is left untouched. Hard-coding either value breaks the other route: 1 would put a
// second h1 on /library whenever the drawer opens, 3 leaves /documents/:id with no h1 at all.
// `size` is pinned to "h3" for both, so neither site moves a pixel.
function isoDate(iso: string | null): string {
  return iso ? iso.slice(0, 10) : "—";
}

export function ArtifactHeader({
  doc,
  typeName,
  ownerName,
  order = 3,
}: {
  doc: DocumentSummary;
  typeName?: string;
  ownerName?: string;
  order?: 1 | 3;
}) {
  return (
    <Stack gap="xs">
      <Group gap="sm" align="center">
        <Text ff="monospace" fw={600} size="sm">
          {doc.identifier}
        </Text>
        <StateBadge state={doc.current_state} size="lg" />
      </Group>
      <Title order={order} size="h3">
        {doc.title}
      </Title>
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
            <ClauseBadge key={c} clause={c} />
          ))}
        </Group>
      )}
    </Stack>
  );
}
