import { Anchor, Container, Group, Table, Text, Title } from "@mantine/core";
import { Link } from "react-router-dom";
import { AsOf } from "../../lib/AsOf";
import { StatusBadge } from "../../lib/StatusBadge";
import { ErrorState, LoadingState, NoAccessState } from "../../lib/states";
import { CoverageBadge } from "./CoverageBadge";
import { useComplianceChecklist } from "./useComplianceChecklist";

export function CompliancePage() {
  const { data, isLoading, isError, forbidden, dataUpdatedAt, refetch } = useComplianceChecklist();

  if (forbidden) {
    return (
      <Container size="md" py="md">
        <Title order={2} mb="md">
          Compliance Checklist
        </Title>
        <NoAccessState
          message={
            <>
              You don&rsquo;t have access to the Compliance Checklist. It&rsquo;s available to the
              Quality Manager and Internal Auditor roles.
            </>
          }
        />
      </Container>
    );
  }
  if (isLoading) {
    return (
      <Container size="md" py="md">
        <LoadingState label="Loading the checklist" />
      </Container>
    );
  }
  if (isError || !data) {
    return (
      <Container size="md" py="md">
        <Title order={2} mb="md">
          Compliance Checklist
        </Title>
        <ErrorState title="Couldn't load the checklist" onRetry={() => refetch()} />
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
        ★ mandatory-clause coverage ({data.framework}). Status against a rule — not a compliance
        verdict.
      </Text>
      <AsOf at={dataUpdatedAt} />
      <Group gap="sm" mb="md" mt={4} aria-label="Coverage rollup">
        <Text fw={600}>{rollup.total} mandatory items:</Text>
        <Text>✓ Covered: {rollup.covered}</Text>
        <Text>◔ Partial: {rollup.partial}</Text>
        <Text>✕ Gap: {rollup.gap}</Text>
        <Text>⏰ Review overdue: {rollup.overdue_review}</Text>
      </Group>
      <Table.ScrollContainer minWidth={720}>
        <Table striped highlightOnHover>
          <Table.Thead>
            <Table.Tr>
              <Table.Th scope="col">Clause</Table.Th>
              <Table.Th scope="col">Title</Table.Th>
              <Table.Th scope="col">Phase</Table.Th>
              <Table.Th scope="col">Mapped</Table.Th>
              <Table.Th scope="col">Effective</Table.Th>
              <Table.Th scope="col">Status</Table.Th>
              <Table.Th scope="col">Review</Table.Th>
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
                <Table.Td>
                  {r.overdue_review ? (
                    <StatusBadge tone="danger" label="Overdue" kind="Review" />
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
      </Table.ScrollContainer>
    </Container>
  );
}
