import { Anchor, Button, Container, Group, Table, Text, Title } from "@mantine/core";
import { useState } from "react";
import { Link } from "react-router-dom";
import { usePermissions } from "../../app/shell/usePermissions";
import { AsOf } from "../../lib/AsOf";
import { EmptyState, ErrorState, LoadingState, NoAccessState } from "../../lib/states";
import type { Complaint } from "../../lib/types";
import { SEVERITY_LABEL } from "./columns";
import { ComplaintForm } from "./ComplaintForm";
import { useComplaints } from "./hooks";
import { SpawnCapaModal } from "./SpawnCapaModal";

export function ComplaintsPage() {
  const { data, isLoading, isError, forbidden, dataUpdatedAt, refetch } = useComplaints();
  const { can } = usePermissions();
  const [formOpen, setFormOpen] = useState(false);
  const [spawnComplaint, setSpawnComplaint] = useState<Complaint | null>(null);

  if (forbidden) {
    return (
      <Container size="lg" py="md">
        <Title order={3} mb="md">
          Complaints
        </Title>
        <NoAccessState
          message={
            <>
              You don't have access to complaints. They're available to roles holding{" "}
              <code>record.read</code>.
            </>
          }
        />
      </Container>
    );
  }
  if (isLoading) {
    return (
      <Container size="lg" py="md">
        <LoadingState label="Loading complaints" />
      </Container>
    );
  }
  if (isError) {
    return (
      <Container size="lg" py="md">
        <Title order={3} mb="md">
          Complaints
        </Title>
        <ErrorState title="Couldn't load complaints" onRetry={() => refetch()} />
      </Container>
    );
  }

  const rows = data ?? [];
  return (
    <Container size="lg" py="md">
      <Group justify="space-between" mb="md">
        <Title order={3}>Complaints</Title>
        {can("record.create") && (
          <Button onClick={() => setFormOpen(true)}>＋ Log complaint</Button>
        )}
      </Group>
      <AsOf at={dataUpdatedAt} />
      {rows.length === 0 ? (
        <EmptyState message="No complaints logged yet." />
      ) : (
        <Table striped highlightOnHover>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Identifier</Table.Th>
              <Table.Th>Customer</Table.Th>
              <Table.Th>Channel</Table.Th>
              <Table.Th>Severity</Table.Th>
              <Table.Th>Description</Table.Th>
              <Table.Th>CAPA</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {rows.map((c) => (
              <Table.Tr key={c.id}>
                <Table.Td>{c.identifier ?? "—"}</Table.Td>
                <Table.Td>{c.customer ?? "—"}</Table.Td>
                <Table.Td>{c.channel ?? "—"}</Table.Td>
                <Table.Td>{c.severity ? SEVERITY_LABEL[c.severity] : "—"}</Table.Td>
                <Table.Td>
                  <Text lineClamp={2}>{c.description}</Text>
                </Table.Td>
                <Table.Td>
                  {c.spawned_capa_id ? (
                    <Anchor component={Link} to={`/capa?capa=${c.spawned_capa_id}`}>
                      View CAPA
                    </Anchor>
                  ) : can("capa.create") ? (
                    <Button size="xs" variant="light" onClick={() => setSpawnComplaint(c)}>
                      Spawn CAPA
                    </Button>
                  ) : (
                    <Text c="dimmed" size="sm">
                      —
                    </Text>
                  )}
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}
      <ComplaintForm opened={formOpen} onClose={() => setFormOpen(false)} />
      {spawnComplaint && (
        <SpawnCapaModal complaint={spawnComplaint} opened onClose={() => setSpawnComplaint(null)} />
      )}
    </Container>
  );
}
