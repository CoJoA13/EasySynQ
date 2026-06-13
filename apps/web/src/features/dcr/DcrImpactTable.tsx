import { Table, Text } from "@mantine/core";
import type { DcrImpact } from "../../lib/types";

function summarizeAuto(auto: Record<string, unknown> | null): string {
  if (!auto) return "—";
  if (auto.applicable === false) return "Not applicable";
  const processes = Array.isArray(auto.processes) ? auto.processes.length : null;
  if (processes !== null) return `Applicable · ${processes} process${processes === 1 ? "" : "es"}`;
  return "Applicable";
}

export function DcrImpactTable({ impact }: { impact: DcrImpact[] }) {
  if (impact.length === 0) {
    return (
      <Text size="sm" c="dimmed">
        Not yet assessed.
      </Text>
    );
  }
  return (
    <Table>
      <Table.Thead>
        <Table.Tr>
          <Table.Th>Dimension</Table.Th>
          <Table.Th>System facts</Table.Th>
          <Table.Th>Annotation</Table.Th>
        </Table.Tr>
      </Table.Thead>
      <Table.Tbody>
        {impact.map((i) => (
          <Table.Tr key={i.id}>
            <Table.Td>{i.dimension}</Table.Td>
            <Table.Td>{summarizeAuto(i.auto_populated)}</Table.Td>
            <Table.Td>{i.requester_annotation ?? "—"}</Table.Td>
          </Table.Tr>
        ))}
      </Table.Tbody>
    </Table>
  );
}
