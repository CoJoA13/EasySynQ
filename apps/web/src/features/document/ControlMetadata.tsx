import { Anchor, Group, Table, Text } from "@mantine/core";
import type { DocumentSummary } from "../../lib/types";
import { ReviewStateBadge } from "./ReviewStateBadge";

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
  onEditReviewPeriod,
}: {
  doc: DocumentSummary;
  typeName?: string;
  ownerName?: string;
  // S-web-8: the detail page passes this iff doc.capabilities.manage_metadata (capabilities are
  // detail-only, so the drawer never renders the affordance).
  onEditReviewPeriod?: () => void;
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
        <Row
          label="Review period"
          value={
            <Group gap="xs">
              <Text size="sm">
                {doc.review_period_months !== null ? `${doc.review_period_months} months` : "—"}
              </Text>
              {onEditReviewPeriod && (
                <Anchor
                  component="button"
                  type="button"
                  size="sm"
                  aria-label="Edit review period"
                  onClick={onEditReviewPeriod}
                >
                  Edit
                </Anchor>
              )}
            </Group>
          }
        />
        <Row
          label="Next review"
          value={
            doc.next_review_due ? (
              <Group gap="xs">
                <Text size="sm">{doc.next_review_due}</Text>
                <ReviewStateBadge state={doc.review_state} />
              </Group>
            ) : (
              "—"
            )
          }
        />
        <Row
          label="Last reviewed"
          value={doc.last_reviewed_at ? doc.last_reviewed_at.slice(0, 10) : "—"}
        />
      </Table.Tbody>
    </Table>
  );
}
