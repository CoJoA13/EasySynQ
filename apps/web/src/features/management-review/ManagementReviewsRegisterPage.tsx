import { Alert, Anchor, Badge, Button, Container, Group, Loader, Table, Text, Title } from "@mantine/core";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { usePermissions } from "../../app/shell/usePermissions";
import { StateBadge } from "../document/StateBadge";
import { useMgmtReviews } from "./hooks";
import { NewManagementReviewModal } from "./NewManagementReviewModal";

export function ManagementReviewsRegisterPage() {
  const { data, isLoading, isError, forbidden } = useMgmtReviews();
  const { can } = usePermissions();
  const navigate = useNavigate();
  const [createOpen, setCreateOpen] = useState(false);

  if (forbidden) {
    return (
      <Container size="lg" py="md">
        <Title order={2} mb="md">Management reviews</Title>
        <Alert color="gray" title="No access">
          You don't have access to Management Reviews. It's available to the Quality Manager.
        </Alert>
      </Container>
    );
  }
  if (isError) {
    return (
      <Container size="lg" py="md">
        <Title order={2} mb="md">Management reviews</Title>
        <Alert color="red" title="Couldn't load management reviews">Please try again.</Alert>
      </Container>
    );
  }
  if (isLoading || !data) {
    return <Container size="lg" py="md"><Loader /></Container>;
  }
  return (
    <Container size="lg" py="md">
      <Group justify="space-between" mb="md">
        <Title order={2}>Management reviews</Title>
        {can("mgmtReview.create") && (
          <Button onClick={() => setCreateOpen(true)}>New management review</Button>
        )}
      </Group>
      {data.data.length === 0 ? (
        <Alert color="gray" title="No management reviews yet" mt="md">
          {can("mgmtReview.create")
            ? "Convene the first management review to record clause 9.3 minutes."
            : "No management reviews have been convened yet."}
        </Alert>
      ) : (
        <Table striped highlightOnHover mt="md">
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Ref</Table.Th><Table.Th>Review</Table.Th><Table.Th>Period</Table.Th>
              <Table.Th>Review date</Table.Th><Table.Th>Status</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {data.data.map((mr) => (
              <Table.Tr key={mr.id}>
                <Table.Td>
                  <Group gap="xs" wrap="nowrap">
                    <Anchor component={Link} to={`/management-reviews/${mr.id}`}>{mr.identifier}</Anchor>
                    {/* The steady state (Effective) stays unmarked; every other state gets the chip. */}
                    {mr.current_state !== "Effective" && <StateBadge state={mr.current_state} size="xs" />}
                  </Group>
                </Table.Td>
                <Table.Td><Text lineClamp={1}>{mr.title}</Text></Table.Td>
                <Table.Td>{mr.period_label ?? "—"}</Table.Td>
                <Table.Td>{mr.review_date ?? "—"}</Table.Td>
                <Table.Td>
                  {mr.close_state ? (
                    <Badge variant="light" color={mr.close_state === "Closed" ? "gray" : "blue"}>
                      {mr.close_state === "Closed" ? "Closed" : "Actions tracked"}
                    </Badge>
                  ) : "—"}
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}
      {createOpen && (
        <NewManagementReviewModal
          opened
          onClose={() => setCreateOpen(false)}
          onCreated={(id) => { setCreateOpen(false); navigate(`/management-reviews/${id}`); }}
        />
      )}
    </Container>
  );
}
