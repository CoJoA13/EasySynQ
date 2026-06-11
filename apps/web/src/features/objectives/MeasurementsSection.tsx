import { Alert, Group, Loader, Stack, Table, Text, Title } from "@mantine/core";
import { useObjectiveMeasurements } from "./hooks";

export function MeasurementsSection({ objectiveId, unit }: { objectiveId: string; unit: string }) {
  const { data, isLoading, forbidden } = useObjectiveMeasurements(objectiveId);
  void unit; // available for the Record modal in Task 13
  return (
    <Stack gap="sm">
      <Group justify="space-between">
        <Title order={4}>Measurement history</Title>
        {/* The Record-measurement button (gated kpi.record) is wired in Task 13. */}
      </Group>
      {forbidden ? (
        <Alert color="gray" title="No access">
          You don't have access to the measurement history for this objective.
        </Alert>
      ) : isLoading ? (
        <Loader />
      ) : (data ?? []).length === 0 ? (
        <Text c="dimmed" size="sm">No measurements recorded yet.</Text>
      ) : (
        <>
          <Table>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Period</Table.Th>
                <Table.Th>Value</Table.Th>
                <Table.Th>Target then</Table.Th>
                <Table.Th>Source</Table.Th>
                <Table.Th>Recorded</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {(data ?? []).map((m) => (
                <Table.Tr key={m.id}>
                  <Table.Td>{m.period}</Table.Td>
                  <Table.Td>{m.value} {m.unit}</Table.Td>
                  <Table.Td c="dimmed">{m.target_at_capture} {m.unit}</Table.Td>
                  <Table.Td c="dimmed">{m.source ?? "—"}</Table.Td>
                  <Table.Td c="dimmed">{m.created_at.slice(0, 10)}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
          <Text c="dimmed" size="xs">Readings are append-only. Trend charts arrive in a later release.</Text>
        </>
      )}
    </Stack>
  );
}
