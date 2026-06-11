import { Alert, Button, Group, Loader, Stack, Table, Text, Title } from "@mantine/core";
import { useState } from "react";
import { usePermissions } from "../../app/shell/usePermissions";
import { useObjectiveMeasurements } from "./hooks";
import { RecordMeasurementModal } from "./RecordMeasurementModal";

export function MeasurementsSection({ objectiveId, unit }: { objectiveId: string; unit: string }) {
  const { data, isLoading, forbidden } = useObjectiveMeasurements(objectiveId);
  const { can } = usePermissions();
  const [open, setOpen] = useState(false);
  return (
    <Stack gap="sm">
      <Group justify="space-between">
        <Title order={3}>Measurement history</Title>
        {can("kpi.record") && <Button size="xs" onClick={() => setOpen(true)}>Record measurement</Button>}
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
      <RecordMeasurementModal
        opened={open}
        objectiveId={objectiveId}
        unit={unit}
        onClose={() => setOpen(false)}
        onRecorded={() => setOpen(false)}
      />
    </Stack>
  );
}
