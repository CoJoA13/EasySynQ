import { Button, Stack, Table, Text } from "@mantine/core";
import { useState } from "react";
import { useApi } from "../../lib/api";
import type { DocumentSummary } from "../../lib/types";

// The Overview tab: the control-metadata definition list (DP-5 identity in durable form) + the
// controlled-copy download (Effective-only; document.read — every reader holds it). The artifact
// header itself renders above the tabs (DocumentDrawer), so it is not repeated here.
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

export function OverviewTab({
  doc,
  typeName,
  ownerName,
}: {
  doc: DocumentSummary;
  typeName?: string;
  ownerName?: string;
}) {
  const api = useApi();
  const [downloading, setDownloading] = useState(false);

  async function download() {
    setDownloading(true);
    try {
      const { download_url } = await api.get<{ download_url: string }>(
        `/api/v1/documents/${doc.id}/download`,
      );
      window.open(download_url, "_blank", "noopener,noreferrer");
    } catch {
      /* quiet — a transient presign failure is non-fatal for a read-only view */
    } finally {
      setDownloading(false);
    }
  }

  return (
    <Stack gap="sm">
      <Table withRowBorders={false}>
        <Table.Tbody>
          <Row label="Identifier" value={<Text ff="monospace" size="sm">{doc.identifier}</Text>} />
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
      {doc.current_effective_version_id && (
        <Button
          variant="light"
          size="sm"
          loading={downloading}
          onClick={download}
          style={{ alignSelf: "flex-start" }}
        >
          ⤓ Download controlled copy
        </Button>
      )}
    </Stack>
  );
}
