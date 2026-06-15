import {
  Alert,
  Anchor,
  Container,
  Group,
  Loader,
  Pagination,
  Stack,
  Table,
  Text,
  Title,
} from "@mantine/core";
import { useState } from "react";
import { Link } from "react-router-dom";
import { formatTimestamp } from "../../lib/time";
import { SUPERSEDED_PAGE_SIZE, useSupersededCopies } from "./hooks";

// S-web-8: the D4 recall list (doc 05 §9.1 / R11) — outstanding EXPORTED/PRINTED copies of
// now-superseded versions. No decrement leg exists (a paper copy can't be un-printed): the count is
// the honest upper bound; the /verify token is the per-copy resolution. Server-side pagination
// (offset/limit) — no virtualization (the S-ing-4b rule).
export function SupersededCopiesPage() {
  const [page, setPage] = useState(1);
  const { data, isLoading, isError, forbidden } = useSupersededCopies(
    (page - 1) * SUPERSEDED_PAGE_SIZE,
  );

  if (forbidden) {
    return (
      <Container size="lg" py="md">
        <Title order={2} mb="md">
          Superseded copies
        </Title>
        <Alert color="gray" title="No access">
          You don&rsquo;t have access to the drift status surface. It requires the drift.read
          permission (System Administrator).
        </Alert>
      </Container>
    );
  }
  if (isLoading) {
    return (
      <Container size="lg" py="md">
        <Loader aria-label="Loading superseded copies" />
      </Container>
    );
  }
  if (isError || !data) {
    return (
      <Container size="lg" py="md">
        <Title order={2} mb="md">
          Superseded copies
        </Title>
        <Alert color="red" title="Couldn't load the report">
          Please try again.
        </Alert>
      </Container>
    );
  }

  const pages = Math.max(1, Math.ceil(data.total.versions / SUPERSEDED_PAGE_SIZE));
  return (
    <Container size="lg" py="md">
      <Stack gap="md">
        <div>
          <Title order={2}>Superseded copies</Title>
          <Text c="dimmed" size="sm">
            Exported/printed copies of versions that have since been superseded or obsoleted —{" "}
            {data.total.versions} versions · {data.total.copies} copies outstanding. Use this as the
            recall list; each paper copy resolves via its verify QR.
          </Text>
        </div>
        {data.items.length === 0 ? (
          <Text c="dimmed">No outstanding copies of superseded versions.</Text>
        ) : (
          <Table striped highlightOnHover aria-label="Superseded copies">
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Identifier</Table.Th>
                <Table.Th>Copied revision</Table.Th>
                <Table.Th>State</Table.Th>
                <Table.Th>Current revision</Table.Th>
                <Table.Th>Exported</Table.Th>
                <Table.Th>Printed</Table.Th>
                <Table.Th>Last copy</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {data.items.map((r) => (
                <Table.Tr key={r.version_id}>
                  <Table.Td>
                    <Anchor
                      component={Link}
                      to={`/documents/${r.document_id}`}
                      ff="monospace"
                      size="sm"
                    >
                      {r.identifier}
                    </Anchor>
                  </Table.Td>
                  <Table.Td>{r.revision_label}</Table.Td>
                  <Table.Td>{r.version_state}</Table.Td>
                  <Table.Td>{r.current_revision_label ?? "—"}</Table.Td>
                  <Table.Td>{r.exported}</Table.Td>
                  <Table.Td>{r.printed}</Table.Td>
                  <Table.Td>{formatTimestamp(r.last_copy_at)}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        )}
        {pages > 1 && (
          <Group justify="center">
            <Pagination value={page} onChange={setPage} total={pages} />
          </Group>
        )}
      </Stack>
    </Container>
  );
}
