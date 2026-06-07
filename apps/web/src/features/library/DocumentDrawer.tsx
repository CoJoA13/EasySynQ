import { Stack, Table, Title } from "@mantine/core";
import { DetailDrawer } from "../../app/shell/DetailDrawer";
import type { DocumentSummary } from "../../lib/types";
import { StateBadge } from "./StateBadge";

// Slice-1 overview: the fuller raw field set from the list row (no extra fetch). Friendly
// type/owner names + version timeline + tabs arrive in Slice 2.
export function DocumentDrawer({
  doc,
  opened,
  onClose,
}: {
  doc: DocumentSummary | null;
  opened: boolean;
  onClose: () => void;
}) {
  return (
    <DetailDrawer opened={opened} onClose={onClose} title={doc?.identifier ?? "Document"}>
      {doc && (
        <Stack gap="sm">
          <Title order={3}>{doc.title}</Title>
          <Table withRowBorders={false}>
            <Table.Tbody>
              <Table.Tr><Table.Td>State</Table.Td><Table.Td><StateBadge state={doc.current_state} /></Table.Td></Table.Tr>
              <Table.Tr><Table.Td>Classification</Table.Td><Table.Td>{doc.classification}</Table.Td></Table.Tr>
              <Table.Tr><Table.Td>Folder</Table.Td><Table.Td>{doc.folder_path ?? "—"}</Table.Td></Table.Tr>
              <Table.Tr><Table.Td>Clauses</Table.Td><Table.Td>{(doc.clause_refs ?? []).join(", ") || "—"}</Table.Td></Table.Tr>
              <Table.Tr><Table.Td>Created</Table.Td><Table.Td>{doc.created_at ?? "—"}</Table.Td></Table.Tr>
              <Table.Tr><Table.Td>Owner (id)</Table.Td><Table.Td>{doc.owner_user_id}</Table.Td></Table.Tr>
            </Table.Tbody>
          </Table>
        </Stack>
      )}
    </DetailDrawer>
  );
}
