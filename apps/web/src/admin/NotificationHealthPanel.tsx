import {
  Alert,
  Button,
  Card,
  Group,
  Modal,
  SimpleGrid,
  Stack,
  Table,
  Text,
  Title,
} from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import { AsOf } from "../lib/AsOf";
import { EmptyState, ErrorState, LoadingState, MutationErrorState } from "../lib/states";
import { TONE_GLYPH } from "../lib/status";
import { formatRelativeTime } from "../lib/time";
import { useNotificationHealth, useRequeueFailed } from "./hooks";

function relAge(iso: string | null): string {
  return iso ? formatRelativeTime(new Date(iso).getTime()) : "—";
}

export function NotificationHealthPanel() {
  const health = useNotificationHealth();
  const [confirmOpen, confirm] = useDisclosure(false);
  const requeue = useRequeueFailed();
  if (health.isError) {
    return (
      <ErrorState title="Couldn't load delivery health" onRetry={() => void health.refetch()} />
    );
  }
  if (health.isLoading || !health.data) {
    return <LoadingState label="Loading delivery health" />;
  }
  const h = health.data;
  const failed = h.email.failed;
  const hasPending = h.email.pending_now + h.email.pending_scheduled > 0;
  const doRequeue = () => requeue.mutate(undefined, { onSuccess: () => confirm.close() });
  const closeConfirm = () => {
    requeue.reset();
    confirm.close();
  };
  return (
    <Stack gap="md">
      <Group justify="space-between" align="center">
        <Title order={3}>Email delivery health</Title>
        <Group gap="sm">
          <AsOf at={health.dataUpdatedAt} prefix="Checked" />
          {failed > 0 && (
            <Button variant="light" size="compact-sm" onClick={confirm.open}>
              Requeue failed
            </Button>
          )}
          <Button
            variant="subtle"
            size="compact-sm"
            onClick={() => void health.refetch()}
            loading={health.isFetching}
          >
            Refresh
          </Button>
        </Group>
      </Group>

      {!h.org_email_enabled && (
        <Alert variant="light" color="gray" title="Email delivery is off">
          Email delivery is off for the organisation — no emails are being sent. The counts below
          stay at zero until you enable email above.
        </Alert>
      )}

      <SimpleGrid cols={{ base: 2, sm: 5 }}>
        <Card withBorder padding="sm">
          <Text size="xs" c="dimmed">
            Failed
          </Text>
          <Text
            fw={600}
            c={failed > 0 ? "var(--es-danger-text)" : undefined}
            aria-label={`Email delivery failures: ${failed}`}
          >
            {failed > 0 && <span aria-hidden="true">{TONE_GLYPH.danger} </span>}
            {failed}
          </Text>
        </Card>
        <Card withBorder padding="sm">
          <Text size="xs" c="dimmed">
            Pending now
          </Text>
          <Text fw={600}>{h.email.pending_now}</Text>
        </Card>
        <Card withBorder padding="sm">
          <Text size="xs" c="dimmed">
            Scheduled retry
          </Text>
          <Text fw={600}>{h.email.pending_scheduled}</Text>
        </Card>
        <Card withBorder padding="sm">
          <Text size="xs" c="dimmed">
            Suppressed
          </Text>
          <Text fw={600}>{h.email.suppressed}</Text>
        </Card>
        <Card withBorder padding="sm">
          <Text size="xs" c="dimmed">
            Awareness backlog
          </Text>
          <Text fw={600}>{h.awareness.pending}</Text>
        </Card>
      </SimpleGrid>

      {hasPending && (
        <Text size="sm" c="dimmed">
          Oldest pending email: {relAge(h.email.oldest_pending_at)}
        </Text>
      )}
      {h.awareness.pending > 0 && (
        <Text size="sm" c="dimmed">
          Oldest pending awareness event: {relAge(h.awareness.oldest_pending_at)}
        </Text>
      )}

      <Stack gap="xs">
        <Title order={4}>Recent failures</Title>
        {h.recent_failures.length === 0 ? (
          <EmptyState message="No delivery failures." />
        ) : (
          <Table striped withTableBorder>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Recipient</Table.Th>
                <Table.Th>Error</Table.Th>
                <Table.Th>Attempts</Table.Th>
                <Table.Th>When</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {h.recent_failures.map((f, i) => (
                <Table.Tr key={i}>
                  <Table.Td>{f.recipient_email}</Table.Td>
                  <Table.Td>{f.last_error ?? "—"}</Table.Td>
                  <Table.Td>{f.attempts}</Table.Td>
                  <Table.Td>{relAge(f.failed_at)}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        )}
      </Stack>

      <Modal opened={confirmOpen} onClose={closeConfirm} title="Requeue failed emails">
        <Stack gap="md">
          <Text size="sm">
            Requeue {failed} failed email{failed === 1 ? "" : "s"}? They&apos;ll be retried on the
            next delivery drain.
          </Text>
          {requeue.isError && <MutationErrorState title="Couldn't requeue" error={requeue.error} />}
          <Group justify="flex-end">
            <Button variant="default" size="sm" onClick={closeConfirm}>
              Cancel
            </Button>
            <Button size="sm" onClick={doRequeue} loading={requeue.isPending}>
              Requeue
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Stack>
  );
}
