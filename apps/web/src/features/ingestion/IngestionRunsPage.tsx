import { Alert, Anchor, Button, Group, Skeleton, Stack, Table, Text, Title } from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import { Link, useNavigate } from "react-router-dom";
import { usePermissions } from "../../app/shell/usePermissions";
import { ApiError } from "../../lib/api";
import { ImportStatusBadge } from "./ImportStatusBadge";
import { NewImportModal } from "./NewImportModal";
import { useImportRuns } from "./hooks";

// The runs landing (D-1): a calm list of import runs + a gated New-Import entry. Presentational over
// useImportRuns(); a 403 → the calm no-access panel (import has no hidden_by_scope — full deny).
export function IngestionRunsPage() {
  const { data, isLoading, isError, error } = useImportRuns();
  const { can } = usePermissions();
  const navigate = useNavigate();
  const [modalOpen, modal] = useDisclosure(false);

  const forbidden = error instanceof ApiError && error.status === 403;
  const runs = data ?? [];

  if (forbidden) {
    return (
      <Stack gap="md">
        <Title order={1}>Import</Title>
        <Alert color="gray" title="No access">
          You don't have access to import review.
        </Alert>
      </Stack>
    );
  }

  return (
    <Stack gap="md">
      <Group justify="space-between" align="flex-end">
        <div>
          <Title order={1}>Import</Title>
          <Text size="sm" c="dimmed">
            {isLoading ? "Loading…" : `${runs.length} import run${runs.length === 1 ? "" : "s"}`}
          </Text>
        </div>
        {can("import.execute") && (
          <Button size="sm" onClick={modal.open}>
            ＋ New import
          </Button>
        )}
      </Group>

      {isError && !forbidden && (
        <Alert color="red" title="Couldn't load import runs">
          Please try again.
        </Alert>
      )}

      {isLoading && (
        <Stack gap="xs" aria-label="Loading import runs">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} height={36} />
          ))}
        </Stack>
      )}

      {!isLoading && !isError && runs.length === 0 && <Text>No imports yet.</Text>}

      {!isLoading && !isError && runs.length > 0 && (
        <Table highlightOnHover aria-label="Import runs">
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Source root</Table.Th>
              <Table.Th>Status</Table.Th>
              <Table.Th>Created</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {runs.map((run) => (
              <Table.Tr key={run.id}>
                <Table.Td>
                  <Anchor component={Link} to={`/ingestion/${run.id}`} ff="monospace" size="sm">
                    {run.source_root}
                  </Anchor>
                </Table.Td>
                <Table.Td>
                  <ImportStatusBadge status={run.status} />
                </Table.Td>
                <Table.Td>
                  <Text size="sm">{run.created_at ? run.created_at.slice(0, 10) : "—"}</Text>
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}

      <NewImportModal
        opened={modalOpen}
        onClose={modal.close}
        onCreated={(runId) => {
          modal.close();
          navigate(`/ingestion/${runId}`);
        }}
      />
    </Stack>
  );
}
