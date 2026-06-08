import { Anchor, Badge, Group, Stack, Text } from "@mantine/core";
import { Link } from "react-router-dom";
import type { SearchHit } from "../../lib/types";
import { StateBadge } from "../document/StateBadge";
import { Snippet } from "./Snippet";

export function SearchResultRow({ hit }: { hit: SearchHit }) {
  return (
    <Stack gap={4} py="xs" style={{ borderBottom: "1px solid var(--es-border)" }}>
      <Group gap="sm" wrap="nowrap">
        <Text ff="monospace" size="sm" c="dimmed">
          {hit.identifier}
        </Text>
        <Anchor component={Link} to={`/documents/${hit.id}`} fw={600}>
          {hit.title}
        </Anchor>
        <StateBadge state={hit.current_state} />
      </Group>
      {hit.clause_refs.length > 0 && (
        <Group gap={4}>
          {hit.clause_refs.map((c) => (
            <Anchor key={c} component={Link} to={`/library?clause=${encodeURIComponent(c)}`} underline="never">
              <Badge variant="light" size="sm">
                Clause {c}
              </Badge>
            </Anchor>
          ))}
        </Group>
      )}
      <Snippet text={hit.snippet} />
    </Stack>
  );
}
