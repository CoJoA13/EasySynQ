import { Alert, Anchor, Card, Stack, Table, Text } from "@mantine/core";
import { Link } from "react-router-dom";
import { ApiError } from "../../lib/api";
import { useDocument } from "../document/useDocument";

// S-ack-2: the DOC_ACK task's left column — the document to read, loaded BEST-EFFORT via document.read.
// A 403 degrades calmly and never blocks the attestation card (the obligation stands regardless of read).
export function DocAckContext({ documentId }: { documentId: string }) {
  const { data: doc, isLoading, isError, error } = useDocument(documentId, { enabled: true, retry: false });
  if (isLoading && !doc) return <Text c="dimmed">Loading the document to acknowledge…</Text>;
  if (isError || !doc) {
    const status = error instanceof ApiError ? error.status : 0;
    return (
      <Alert color="yellow" title="Document details aren't visible to you">
        <Text size="sm">
          {status === 403
            ? "You can acknowledge this document, but reading it isn't granted to you here."
            : "Could not load the document to acknowledge."}
        </Text>
      </Alert>
    );
  }
  const governingRev = doc.current_effective_version_id ? "the current Effective revision" : "—";
  return (
    <Card withBorder>
      <Stack gap="sm">
        <div>
          <Text ff="monospace" size="sm">{doc.identifier}</Text>
          <Text fw={600}>{doc.title}</Text>
        </div>
        <Table withRowBorders={false} aria-label="Document context">
          <Table.Tbody>
            <Table.Tr>
              <Table.Td><Text size="sm" c="dimmed">State</Text></Table.Td>
              <Table.Td>{doc.current_state}</Table.Td>
            </Table.Tr>
            <Table.Tr>
              <Table.Td><Text size="sm" c="dimmed">Governing</Text></Table.Td>
              <Table.Td>{governingRev}</Table.Td>
            </Table.Tr>
            <Table.Tr>
              <Table.Td><Text size="sm" c="dimmed">Effective</Text></Table.Td>
              <Table.Td>{doc.effective_from ? doc.effective_from.slice(0, 10) : "—"}</Table.Td>
            </Table.Tr>
          </Table.Tbody>
        </Table>
        <Anchor component={Link} to={`/documents/${doc.id}`} size="sm">Open the document page →</Anchor>
      </Stack>
    </Card>
  );
}
