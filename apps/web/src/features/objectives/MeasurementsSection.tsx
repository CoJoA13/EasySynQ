import { Button, Group, Stack, Table, Text, Title } from "@mantine/core";
import { useState } from "react";
import type { ObjectiveDirection } from "../../lib/types";
import { usePermissions } from "../../app/shell/usePermissions";
import { useObjectiveMeasurements } from "./hooks";
import { EmptyState, ErrorState, LoadingState, NoAccessState } from "../../lib/states";
import { ObjectiveTrendChart } from "./ObjectiveTrendChart";
import { RecordMeasurementModal } from "./RecordMeasurementModal";

export function MeasurementsSection({
  objectiveId,
  unit,
  direction,
}: {
  objectiveId: string;
  unit: string;
  direction?: ObjectiveDirection;
}) {
  const { data, isLoading, isError, forbidden, refetch } = useObjectiveMeasurements(objectiveId);
  const { can } = usePermissions();
  const [open, setOpen] = useState(false);
  return (
    <Stack gap="sm">
      <Group justify="space-between">
        <Title order={3}>Measurement history</Title>
        {can("kpi.record") && (
          <Button size="xs" onClick={() => setOpen(true)}>
            Record measurement
          </Button>
        )}
      </Group>
      {forbidden ? (
        <NoAccessState message="You don't have access to the measurement history for this objective." />
      ) : isError ? (
        <ErrorState
          title="Couldn't load measurements"
          message="Something went wrong loading the measurement history. Please try again."
          onRetry={() => refetch()}
        />
      ) : isLoading ? (
        <LoadingState label="Loading measurements" />
      ) : (data ?? []).length === 0 ? (
        <EmptyState message="No measurements recorded yet." />
      ) : (
        <>
          <ObjectiveTrendChart measurements={data ?? []} unit={unit} direction={direction} />
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
                  <Table.Td>
                    {m.value} {m.unit}
                  </Table.Td>
                  <Table.Td c="dimmed">
                    {m.target_at_capture} {m.unit}
                  </Table.Td>
                  <Table.Td c="dimmed">{m.source ?? "—"}</Table.Td>
                  <Table.Td c="dimmed">{m.created_at.slice(0, 10)}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
          <Text c="dimmed" size="xs">
            Readings are append-only.
          </Text>
        </>
      )}
      {open && (
        <RecordMeasurementModal
          opened
          objectiveId={objectiveId}
          unit={unit}
          onClose={() => setOpen(false)}
          onRecorded={() => setOpen(false)}
        />
      )}
    </Stack>
  );
}
