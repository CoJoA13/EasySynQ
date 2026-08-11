// apps/web/src/features/notifications/NotificationsPage.tsx
import { Button, Container, Group, Stack, Text, Title } from "@mantine/core";
import { isRetryableMutationError } from "../../lib/mutationFeedback";
import { EmptyState, ErrorState, LoadingState, MutationErrorState } from "../../lib/states";
import { useNotifications } from "./hooks";
import { useMarkAllRead } from "./mutations";
import { NotificationItem } from "./NotificationItem";

// The full /notifications history (the popover's "See all"). Server-capped at 50 — a footnote keeps the
// cap honest (no silent truncation). Self-scoped; calm states only (no no-access path).
export function NotificationsPage() {
  const list = useNotifications("all");
  const markAll = useMarkAllRead();
  const rows = list.data ?? [];

  return (
    <Container size="sm" py="xl">
      <Stack gap="md">
        <Group justify="space-between">
          <Title order={1}>Notifications</Title>
          <Button
            variant="light"
            size="compact-sm"
            mih={44}
            onClick={() => markAll.mutate()}
            disabled={markAll.isPending || rows.length === 0}
          >
            Mark all read
          </Button>
        </Group>
        {markAll.isError && (
          <MutationErrorState
            title="Couldn't mark notifications read"
            error={markAll.error}
            onRetry={isRetryableMutationError(markAll.error) ? () => markAll.mutate() : undefined}
            retrying={markAll.isPending}
            onDismiss={markAll.reset}
            retryLabel="Try marking all notifications read again"
            dismissLabel="Dismiss mark-all error"
          />
        )}
        {list.isLoading ? (
          <LoadingState label="Loading notifications" />
        ) : list.isError ? (
          <ErrorState title="Couldn't load notifications" onRetry={() => void list.refetch()} />
        ) : rows.length === 0 ? (
          <EmptyState message="You're all caught up." />
        ) : (
          <Stack gap="sm">
            {rows.map((n) => (
              <NotificationItem key={n.id} notification={n} />
            ))}
            {rows.length >= 50 && (
              <Text size="xs" c="dimmed">
                Showing the 50 most recent notifications.
              </Text>
            )}
          </Stack>
        )}
      </Stack>
    </Container>
  );
}
