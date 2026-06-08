import { Table, Text } from "@mantine/core";
import type { DocumentSummary } from "../../lib/types";

// The control-metadata definition list (DP-5 identity in durable form). Extracted from OverviewTab
// (S-web-2) so the drawer Overview tab AND the standalone Document page's metadata card share one
// presentational source — the rendered rows are identical to the original OverviewTab table.
function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <Table.Tr>
      <Table.Td>
        <Text size="sm" c="dimmed">
          {label}
        </Text>
      </Table.Td>
      <Table.Td>{value}</Table.Td>
    </Table.Tr>
  );
}

export function ControlMetadata({
  doc,
  typeName,
  ownerName,
}: {
  doc: DocumentSummary;
  typeName?: string;
  ownerName?: string;
}) {
  return (
    <Table withRowBorders={false}>
      <Table.Tbody>
        <Row
          label="Identifier"
          value={
            <Text ff="monospace" size="sm">
              {doc.identifier}
            </Text>
          }
        />
        <Row label="State" value={doc.current_state} />
        <Row label="Revision" value={doc.current_effective_version_id ? "Governing" : "—"} />
        <Row label="Owner" value={ownerName ?? "—"} />
        <Row label="Type" value={typeName ?? "—"} />
        <Row label="Classification" value={doc.classification} />
        <Row label="Clauses" value={(doc.clause_refs ?? []).join(", ") || "—"} />
        <Row label="Folder" value={doc.folder_path ?? "—"} />
        <Row label="Effective" value={doc.effective_from ? doc.effective_from.slice(0, 10) : "—"} />
      </Table.Tbody>
    </Table>
  );
}
