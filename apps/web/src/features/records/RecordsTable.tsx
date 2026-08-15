import { Anchor, Table, Text } from "@mantine/core";
import { Link, useLocation } from "react-router-dom";
import { formatTimestamp } from "../../lib/time";
import { humanizeToken } from "../../lib/labels";
import type { RecordSummary } from "../../lib/types";

function stateText(record: RecordSummary): string {
  const glyph = record.disposition_state === "ACTIVE" ? "●" : record.disposition_state === "DUE_FOR_REVIEW" ? "◔" : record.disposition_state === "ON_HOLD" ? "○" : "✓";
  return `${glyph} ${humanizeToken(record.disposition_state)}${record.legal_hold ? " · Legal hold" : ""}`;
}

export function RecordsTable({ records }: { records: RecordSummary[] }) {
  const location = useLocation();
  return <Table.ScrollContainer minWidth={840}><Table striped highlightOnHover mt="md"><Table.Thead><Table.Tr>
    <Table.Th scope="col">Identifier</Table.Th><Table.Th scope="col">Title</Table.Th><Table.Th scope="col">Type</Table.Th><Table.Th scope="col">Captured by</Table.Th><Table.Th scope="col">Captured</Table.Th><Table.Th scope="col">State</Table.Th>
  </Table.Tr></Table.Thead><Table.Tbody>{records.map((record) => <Table.Tr key={record.id}>
    <Table.Td><Anchor component={Link} to={`/records/${record.id}`} state={{ from: `${location.pathname}${location.search}` }} data-rownav aria-label={`Open record ${record.identifier ?? record.id}`}>{record.identifier ?? "Record"}</Anchor></Table.Td>
    <Table.Td><Text lineClamp={1}>{record.title}</Text></Table.Td><Table.Td>{humanizeToken(record.record_type)}</Table.Td><Table.Td>{record.captured_by_display_name ?? "—"}</Table.Td><Table.Td>{record.captured_at ? formatTimestamp(record.captured_at) : "—"}</Table.Td><Table.Td>{stateText(record)}</Table.Td>
  </Table.Tr>)}</Table.Tbody></Table></Table.ScrollContainer>;
}
