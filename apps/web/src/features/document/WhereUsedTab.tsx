import { Loader, Stack, Text } from "@mantine/core";
import type { WhereUsed, WhereUsedLink } from "../../lib/types";
import { useWhereUsed } from "./useWhereUsed";

// The Where-used tab: the doc-05 §7.2 dependency categories (read-only). Neighbour titles are
// resolved server-side, so this is pure display. Only non-empty categories render.
function LinkGroup({ title, links }: { title: string; links: WhereUsedLink[] }) {
  if (links.length === 0) return null;
  return (
    <Stack gap={2}>
      <Text size="xs" fw={700} c="dimmed" tt="uppercase">
        {title}
      </Text>
      {links.map((l) => (
        <Text key={l.link_id} size="sm">
          <Text span ff="monospace" size="sm">
            {l.identifier}
          </Text>{" "}
          — {l.title} <Text span c="dimmed">({l.current_state})</Text>
        </Text>
      ))}
    </Stack>
  );
}

function hasAny(w: WhereUsed): boolean {
  return (
    w.processes.length > 0 ||
    w.child_documents.length > 0 ||
    w.parent_documents.length > 0 ||
    w.referenced_by.length > 0 ||
    w.references_out.length > 0 ||
    w.forms_templates.length > 0 ||
    w.supersedes.length > 0 ||
    w.superseded_by.length > 0 ||
    w.records_produced_under.count > 0
  );
}

export function WhereUsedTab({
  documentId,
  active,
}: {
  documentId: string | null;
  active: boolean;
}) {
  const { data, isLoading, isError } = useWhereUsed(documentId, active);

  if (isLoading) return <Loader size="sm" aria-label="Loading where-used" />;
  if (isError)
    return (
      <Text size="sm" c="red">
        Could not load where-used.
      </Text>
    );
  if (!data || !hasAny(data))
    return (
      <Text size="sm" c="dimmed">
        Nothing depends on this document yet.
      </Text>
    );

  return (
    <Stack gap="md" aria-label="Where-used">
      {data.processes.length > 0 && (
        <Stack gap={2}>
          <Text size="xs" fw={700} c="dimmed" tt="uppercase">
            Processes
          </Text>
          {data.processes.map((p) => (
            <Text key={p.id} size="sm">
              {p.name} <Text span c="dimmed">({p.state})</Text>
            </Text>
          ))}
        </Stack>
      )}
      <LinkGroup title="Child documents" links={data.child_documents} />
      <LinkGroup title="Parent documents" links={data.parent_documents} />
      <LinkGroup title="Referenced by" links={data.referenced_by} />
      <LinkGroup title="References" links={data.references_out} />
      <LinkGroup title="Forms / templates" links={data.forms_templates} />
      <LinkGroup title="Supersedes" links={data.supersedes} />
      <LinkGroup title="Superseded by" links={data.superseded_by} />
      {data.records_produced_under.count > 0 && (
        <Stack gap={2}>
          <Text size="xs" fw={700} c="dimmed" tt="uppercase">
            Records produced under
          </Text>
          <Text size="sm">{data.records_produced_under.count} record(s)</Text>
        </Stack>
      )}
    </Stack>
  );
}
