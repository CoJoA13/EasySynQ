import { Alert, Avatar, Badge, Card, Group, Stack, Table, Text } from "@mantine/core";
import { usePermissions } from "../../app/shell/usePermissions";
import type { AckStatus } from "../../lib/types";
import { AckCoverageRing } from "./AckCoverageRing";
import { DistributionEditor } from "./DistributionEditor";
import { useAcknowledgements, useDistribution } from "./ackHooks";

const STATUS_COLOR: Record<AckStatus, string> = {
  acknowledged: "green",
  pending: "gray",
  overdue: "red",
};

function initials(name: string | null): string {
  if (!name) return "?";
  return name.split(/\s+/).slice(0, 2).map((p) => p[0]?.toUpperCase() ?? "").join("") || "?";
}

export function AcknowledgementsTab({ documentId, active }: { documentId: string; active: boolean }) {
  // ARTIFACT is the valid scope level for a document (resource_for_scope handles
  // SYSTEM/FRAMEWORK/PROCESS/FOLDER/DOC_CLASS/ARTIFACT; an unknown level silently falls back to
  // SYSTEM). "DOC" would only ever match a SYSTEM-scoped grant, hiding the editor from a holder whose
  // document.distribute is artifact-scoped — stricter than the API's _distribute (ARTIFACT) enforcement.
  const perms = usePermissions({ level: "ARTIFACT", id: documentId });
  const canManage = perms.can("document.distribute");
  const dist = useDistribution(documentId);
  const flagOn = dist.data?.acknowledgement_required ?? false;
  const matrix = useAcknowledgements(documentId, active && canManage && flagOn);

  if (!active) return null;
  if (dist.isLoading) return <Text c="dimmed">Loading acknowledgement coverage…</Text>;
  if (dist.isError) {
    return dist.forbidden ? (
      <Text size="sm" c="dimmed">You don't have access to acknowledgement coverage.</Text>
    ) : (
      <Text size="sm" c="red">Could not load acknowledgement coverage.</Text>
    );
  }

  const pending = (matrix.data ?? []).filter((r) => r.status !== "acknowledged");

  return (
    <Stack gap="lg">
      <Card withBorder>
        <Stack gap="sm">
          <Text fw={600}>Acknowledgement coverage</Text>
          <Text size="sm" c="dimmed">Read-and-understood coverage of the governing revision (Cl 7.3 awareness).</Text>
          <AckCoverageRing coverage={dist.data?.coverage ?? null} />
        </Stack>
      </Card>

      {!canManage ? (
        <Alert color="gray" title="Limited view">
          You can view coverage but not the named acknowledgement matrix or distribution settings.
        </Alert>
      ) : (
        <>
          <Card withBorder>
            <Stack gap="sm">
              <Group justify="space-between">
                <Text fw={600}>Who's acknowledged</Text>
                {pending.length > 0 && (
                  <Group gap="xs" align="center">
                    <Avatar.Group>
                      {pending.slice(0, 4).map((r) => (
                        <Avatar key={r.user_id} radius="xl" size="sm">{initials(r.display_name)}</Avatar>
                      ))}
                      {pending.length > 4 && <Avatar radius="xl" size="sm">+{pending.length - 4}</Avatar>}
                    </Avatar.Group>
                    <Text size="xs" c="dimmed">awaiting acknowledgement</Text>
                  </Group>
                )}
              </Group>
              {matrix.isLoading ? (
                <Text c="dimmed">Loading the matrix…</Text>
              ) : matrix.isError ? (
                <Text size="sm" c="dimmed">
                  {matrix.forbidden ? "You don't have access to the named matrix." : "Could not load the matrix."}
                </Text>
              ) : (matrix.data ?? []).length === 0 ? (
                <Text size="sm" c="dimmed">No one is distributed for acknowledgement yet.</Text>
              ) : (
                <Table aria-label="Acknowledgement matrix" striped>
                  <Table.Thead>
                    <Table.Tr>
                      <Table.Th scope="col">Person</Table.Th>
                      <Table.Th scope="col">Status</Table.Th>
                      <Table.Th scope="col">Acknowledged rev</Table.Th>
                      <Table.Th scope="col">Due</Table.Th>
                    </Table.Tr>
                  </Table.Thead>
                  <Table.Tbody>
                    {(matrix.data ?? []).map((r) => (
                      <Table.Tr key={r.user_id}>
                        <Table.Td>{r.display_name ?? r.user_id}</Table.Td>
                        <Table.Td><Badge color={STATUS_COLOR[r.status]} variant="light">{r.status}</Badge></Table.Td>
                        <Table.Td>{r.acknowledged_revision_label ?? "—"}</Table.Td>
                        <Table.Td>{r.due_at ? r.due_at.slice(0, 10) : "—"}</Table.Td>
                      </Table.Tr>
                    ))}
                  </Table.Tbody>
                </Table>
              )}
            </Stack>
          </Card>
          <DistributionEditor documentId={documentId} payload={dist.data!} />
        </>
      )}
    </Stack>
  );
}
