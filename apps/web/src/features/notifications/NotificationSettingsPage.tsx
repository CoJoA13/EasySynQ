// apps/web/src/features/notifications/NotificationSettingsPage.tsx
import { Button, Container, Group, Stack, Switch, Text, Title } from "@mantine/core";
import { Link } from "react-router-dom";
import { ErrorState, LoadingState, MutationErrorState } from "../../lib/states";
import { useNotificationPreferences } from "./hooks";
import { useSetEmailEnabled } from "./mutations";

// The minimal per-user master email toggle (S-notify-fe). This is the route the email "Manage
// notifications" link (subjects.py::prefs_link → /settings/notifications) targets. The per-event digest
// matrix + quiet hours are a later release. Self-scoped; no permission gate.
export function NotificationSettingsPage() {
  const prefs = useNotificationPreferences();
  const setEmail = useSetEmailEnabled();

  return (
    <Container size="sm" py="xl">
      <Stack gap="md">
        <Group justify="space-between">
          <Title order={1}>Notification settings</Title>
          <Button component={Link} to="/" variant="subtle">
            Back to app
          </Button>
        </Group>
        {prefs.isLoading ? (
          <LoadingState label="Loading preferences" />
        ) : prefs.isError ? (
          <ErrorState title="Couldn't load preferences" onRetry={() => void prefs.refetch()} />
        ) : (
          <Stack gap="sm">
            <Switch
              label="Email notifications"
              aria-label="Email notifications"
              description="Receive an email when work is assigned to you. Emails carry only a summary and a link — never controlled content — and require your administrator to enable email delivery for the organisation."
              checked={prefs.data?.email_enabled ?? true}
              onChange={(e) => setEmail.mutate(e.currentTarget.checked)}
              disabled={setEmail.isPending}
            />
            {setEmail.isError && (
              <MutationErrorState title="Couldn't save your preference" error={setEmail.error} />
            )}
            {setEmail.isSuccess && (
              <Text size="sm" c="dimmed">
                Saved.
              </Text>
            )}
            <Text size="xs" c="dimmed">
              More granular per-event preferences and digests are coming in a later release.
            </Text>
          </Stack>
        )}
      </Stack>
    </Container>
  );
}
