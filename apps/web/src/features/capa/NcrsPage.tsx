import { Alert, Badge, Button, Container, Group, Loader, Table, Text, Title } from "@mantine/core";
import { useState } from "react";
import { usePermissions } from "../../app/shell/usePermissions";
import type { Ncr } from "../../lib/types";
import { SEVERITY_LABEL } from "./columns";
import { DispositionModal } from "./DispositionModal";
import { useNcrs } from "./hooks";
import { DISPOSITION_LABEL, NCR_SOURCE_LABEL } from "./intake";
import { NcrForm } from "./NcrForm";

export function NcrsPage() {
  const { data, isLoading, isError, forbidden } = useNcrs();
  const { can } = usePermissions();
  const [formOpen, setFormOpen] = useState(false);
  const [disposeNcr, setDisposeNcr] = useState<Ncr | null>(null);

  if (forbidden) {
    return (
      <Container size="lg" py="md">
        <Title order={3} mb="md">
          Nonconforming Output (NCR)
        </Title>
        <Alert color="gray" title="No access">
          You don't have access to NCRs. They're available to roles holding <code>ncr.read</code>.
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
          Nonconforming Output (NCR)
        </Title>
        <Alert color="red" title="Couldn't load NCRs">
          Please try again.
        </Alert>
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
        <Text c="dimmed">No NCRs raised yet.</Text>
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
      {disposeNcr && <DispositionModal ncr={disposeNcr} opened onClose={() => setDisposeNcr(null)} />}
    </Container>
  );
}
