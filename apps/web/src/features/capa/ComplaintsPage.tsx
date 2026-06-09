import { Alert, Anchor, Button, Container, Group, Loader, Table, Text, Title } from "@mantine/core";
import { useState } from "react";
import { Link } from "react-router-dom";
import { usePermissions } from "../../app/shell/usePermissions";
import { SEVERITY_LABEL } from "./columns";
import { ComplaintForm } from "./ComplaintForm";
import { useComplaints } from "./hooks";
import { useSpawnCapa } from "./mutations";

export function ComplaintsPage() {
  const { data, isLoading, isError, forbidden } = useComplaints();
  const { can } = usePermissions();
  const spawn = useSpawnCapa();
  const [formOpen, setFormOpen] = useState(false);

  if (forbidden) {
    return (
      <Container size="lg" py="md">
        <Title order={3} mb="md">
          Complaints
        </Title>
        <Alert color="gray" title="No access">
          You don't have access to complaints. They're available to roles holding <code>record.read</code>.
        </Alert>
      </Container>
    );
  }
  if (isLoading) {
    return (
      <Container size="lg" py="md">
        <Loader />
      </Container>
    );
  }
  if (isError) {
    return (
      <Container size="lg" py="md">
        <Title order={3} mb="md">
          Complaints
        </Title>
        <Alert color="red" title="Couldn't load complaints">
          Please try again.
        </Alert>
      </Container>
    );
  }

  const rows = data ?? [];
  return (
    <Container size="lg" py="md">
      <Group justify="space-between" mb="md">
        <Title order={3}>Complaints</Title>
        {can("record.create") && <Button onClick={() => setFormOpen(true)}>＋ Log complaint</Button>}
      </Group>
      {spawn.isError && (
        <Alert color="red" mb="sm">
          Could not spawn a CAPA. Please try again.
        </Alert>
      )}
      {rows.length === 0 ? (
        <Text c="dimmed">No complaints logged yet.</Text>
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
                    <Anchor component={Link} to="/capa">
                      View CAPA
                    </Anchor>
                  ) : can("capa.create") ? (
                    <Button
                      size="xs"
                      variant="light"
                      loading={spawn.isPending && spawn.variables?.complaintId === c.id}
                      onClick={() => spawn.mutate({ complaintId: c.id, severity: c.severity ?? undefined })}
                    >
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
    </Container>
  );
}
