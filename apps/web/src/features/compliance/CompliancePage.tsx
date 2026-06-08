import { Alert, Anchor, Container, Group, Loader, Table, Text, Title } from "@mantine/core";
import { Link } from "react-router-dom";
import { CoverageBadge } from "./CoverageBadge";
import { useComplianceChecklist } from "./useComplianceChecklist";

export function CompliancePage() {
  const { data, isLoading, isError, forbidden } = useComplianceChecklist();

  if (forbidden) {
    return (
      <Container size="md" py="md">
        <Title order={2} mb="md">
          Compliance Checklist
        </Title>
        <Alert color="gray" title="No access">
          You don&rsquo;t have access to the Compliance Checklist. It&rsquo;s available to the Quality
          Manager and Internal Auditor roles.
        </Alert>
      </Container>
    );
  }
  if (isLoading) {
    return (
      <Container size="md" py="md">
        <Loader />
      </Container>
    );
  }
  if (isError || !data) {
    return (
      <Container size="md" py="md">
        <Title order={2} mb="md">
          Compliance Checklist
        </Title>
        <Alert color="red" title="Couldn't load the checklist">
          Please try again.
        </Alert>
      </Container>
    );
  }

  const { rollup, rows } = data;
  return (
    <Container size="lg" py="md">
      <Title order={2} mb="xs">
        Compliance Checklist
      </Title>
      <Text c="dimmed" size="sm" mb="md">
        ★ mandatory-clause coverage ({data.framework}). Status against a rule — not a compliance verdict.
      </Text>
      <Group gap="sm" mb="md" aria-label="Coverage rollup">
        <Text fw={600}>{rollup.total} mandatory items:</Text>
        <Text>✓ Covered: {rollup.covered}</Text>
        <Text>◔ Partial: {rollup.partial}</Text>
        <Text>✕ Gap: {rollup.gap}</Text>
      </Group>
      <Table striped highlightOnHover>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Clause</Table.Th>
            <Table.Th>Title</Table.Th>
            <Table.Th>Phase</Table.Th>
            <Table.Th>Mapped</Table.Th>
            <Table.Th>Effective</Table.Th>
            <Table.Th>Status</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {rows.map((r) => (
            <Table.Tr key={r.clause_id}>
              <Table.Td>
                <Anchor component={Link} to={`/library?clause=${encodeURIComponent(r.number)}`}>
                  ★ {r.number}
                </Anchor>
              </Table.Td>
              <Table.Td>{r.title}</Table.Td>
              <Table.Td>{r.pdca_phase}</Table.Td>
              <Table.Td>{r.mapped_count}</Table.Td>
              <Table.Td>{r.effective_count}</Table.Td>
              <Table.Td>
                <CoverageBadge status={r.status} />
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </Container>
  );
}
