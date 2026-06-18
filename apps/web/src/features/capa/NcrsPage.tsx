import { Badge, Button, Container, Group, Stack, Table, Text, Title } from "@mantine/core";
import { useState } from "react";
import { usePermissions } from "../../app/shell/usePermissions";
import { useUserDirectory } from "../../app/shell/useUserDirectory";
import { EmptyState, ErrorState, LoadingState, NoAccessState } from "../../lib/states";
import type { DirectoryUser, Ncr } from "../../lib/types";
import { SEVERITY_LABEL } from "./columns";
import { DispositionModal } from "./DispositionModal";
import { useNcrs } from "./hooks";
import { DISPOSITION_LABEL, NCR_SOURCE_LABEL } from "./intake";
import { NcrForm } from "./NcrForm";

// Resolve a disposition authorizer's id → display name, degrading to a short id when the directory
// isn't loaded/permitted (the CapaTimeline `actorLabel` pattern — the table is the only NCR surface,
// so it carries the one-shot audit context the backend returns).
function actorLabel(userId: string, directory: DirectoryUser[]): string {
  return directory.find((u) => u.id === userId)?.display_name ?? `${userId.slice(0, 8)}…`;
}

export function NcrsPage() {
  const { data, isLoading, isError, forbidden, refetch } = useNcrs();
  const { can } = usePermissions();
  const { data: directory } = useUserDirectory();
  const [formOpen, setFormOpen] = useState(false);
  const [disposeNcr, setDisposeNcr] = useState<Ncr | null>(null);

  if (forbidden) {
    return (
      <Container size="lg" py="md">
        <Title order={3} mb="md">
          Nonconforming Output (NCR)
        </Title>
        <NoAccessState
          message={
            <>
              You don't have access to NCRs. They're available to roles holding{" "}
              <code>ncr.read</code>.
            </>
          }
        />
      </Container>
    );
  }
  if (isLoading) {
    return (
      <Container size="lg" py="md">
        <LoadingState label="Loading NCRs" />
      </Container>
    );
  }
  if (isError) {
    return (
      <Container size="lg" py="md">
        <Title order={3} mb="md">
          Nonconforming Output (NCR)
        </Title>
        <ErrorState title="Couldn't load NCRs" onRetry={() => refetch()} />
      </Container>
    );
  }

  const rows = data ?? [];
  return (
    <Container size="lg" py="md">
      <Group justify="space-between" mb="md">
        <Title order={3}>Nonconforming Output (NCR)</Title>
        {can("ncr.create") && <Button onClick={() => setFormOpen(true)}>＋ Raise NCR</Button>}
      </Group>
      {rows.length === 0 ? (
        <EmptyState message="No NCRs raised yet." />
      ) : (
        <Table striped highlightOnHover>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Identifier</Table.Th>
              <Table.Th>Source</Table.Th>
              <Table.Th>Severity</Table.Th>
              <Table.Th>Description</Table.Th>
              <Table.Th>Disposition</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {rows.map((n) => (
              <Table.Tr key={n.id}>
                <Table.Td>{n.identifier}</Table.Td>
                <Table.Td>{NCR_SOURCE_LABEL[n.source]}</Table.Td>
                <Table.Td>{SEVERITY_LABEL[n.severity]}</Table.Td>
                <Table.Td>
                  <Text lineClamp={2}>{n.description}</Text>
                </Table.Td>
                <Table.Td>
                  {n.disposition ? (
                    <Stack gap={2}>
                      <Group gap="xs">
                        <Badge variant="light" color="gray">
                          {DISPOSITION_LABEL[n.disposition]}
                        </Badge>
                        {n.disposition_notes && (
                          <Text size="sm" c="dimmed">
                            {n.disposition_notes}
                          </Text>
                        )}
                      </Group>
                      {(n.disposition_authorized_by || n.disposed_at) && (
                        <Text size="xs" c="dimmed">
                          {n.disposition_authorized_by &&
                            `by ${actorLabel(n.disposition_authorized_by, directory ?? [])}`}
                          {n.disposition_authorized_by && n.disposed_at && " · "}
                          {n.disposed_at && new Date(n.disposed_at).toISOString().slice(0, 10)}
                        </Text>
                      )}
                    </Stack>
                  ) : can("ncr.record_correction") ? (
                    <Button size="xs" variant="light" onClick={() => setDisposeNcr(n)}>
                      Record disposition
                    </Button>
                  ) : (
                    <Text c="dimmed" size="sm">
                      Pending
                    </Text>
                  )}
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}
      <NcrForm opened={formOpen} onClose={() => setFormOpen(false)} />
      {disposeNcr && (
        <DispositionModal ncr={disposeNcr} opened onClose={() => setDisposeNcr(null)} />
      )}
    </Container>
  );
}
