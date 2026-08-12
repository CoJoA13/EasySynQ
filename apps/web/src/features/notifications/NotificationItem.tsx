// apps/web/src/features/notifications/NotificationItem.tsx
import { ActionIcon, Anchor, Box, Group, Stack, Text, VisuallyHidden } from "@mantine/core";
import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import { isRetryableMutationError } from "../../lib/mutationFeedback";
import { MutationErrorState } from "../../lib/states";
import { formatRelativeTime, formatTimestamp } from "../../lib/time";
import type { Notification } from "../../lib/types";
import { toRoutePath } from "./deepLink";
import { useMarkRead, useMarkReadOnOpen } from "./mutations";

// One notification row. Unread is carried by a dot glyph + a "Unread" screen-reader label + a bold
// title (never colour alone, DP-5). The row is a Link (semantic navigation) whose accessible name is
// computed from its content — including the VisuallyHidden "Unread" — so we deliberately set NO explicit
// aria-label on it (an explicit name would swallow the nested "Unread"). The "Mark read" ActionIcon is a
// SIBLING of the Link (never nested inside it) so there is no nested-interactive markup; its aria-label
// embeds the title for a unique accessible name. Clicking the row marks read + navigates (popover closes
// via onNavigate); the body is rendered as a plain text node (no dangerouslySetInnerHTML).
export function NotificationItem({
  notification,
  onNavigate,
}: {
  notification: Notification;
  onNavigate?: () => void;
}) {
  const markRead = useMarkRead();
  const markReadOnOpen = useMarkReadOnOpen(notification.title);
  const [markReadFailure, setMarkReadFailure] = useState<{
    error: unknown;
    notificationId: string;
  } | null>(null);
  const markReadAttemptGeneration = useRef(0);
  const markReadInFlightRef = useRef(false);
  const [markReadInFlight, setMarkReadInFlight] = useState(false);
  const unread = notification.read_at === null;

  function markExplicitlyRead(notificationId: string) {
    if (markReadInFlightRef.current) return;

    markReadInFlightRef.current = true;
    setMarkReadInFlight(true);
    const attemptGeneration = ++markReadAttemptGeneration.current;
    markRead.mutate(notificationId, {
      onError: (error) => {
        if (markReadAttemptGeneration.current === attemptGeneration) {
          setMarkReadFailure({ error, notificationId });
        }
      },
      onSuccess: () => {
        if (markReadAttemptGeneration.current === attemptGeneration) {
          setMarkReadFailure(null);
        }
      },
      onSettled: () => {
        markReadInFlightRef.current = false;
        setMarkReadInFlight(false);
      },
    });
  }

  function dismissMarkReadFailure() {
    markReadAttemptGeneration.current += 1;
    setMarkReadFailure(null);
  }

  function open() {
    if (unread) markReadOnOpen.mutate(notification.id);
    onNavigate?.();
  }

  return (
    <Stack gap="xs">
      <Group wrap="nowrap" gap="xs" align="flex-start">
        <Anchor
          component={Link}
          to={toRoutePath(notification.deep_link)}
          onClick={open}
          underline="never"
          c="inherit"
          style={{ flex: 1, minWidth: 0 }}
        >
          <Group wrap="nowrap" gap="xs" align="flex-start">
            {unread && (
              <Box
                w={8}
                h={8}
                mt={6}
                style={{
                  background: "var(--mantine-primary-color-filled)",
                  borderRadius: "50%",
                  flexShrink: 0,
                }}
              >
                <VisuallyHidden>Unread</VisuallyHidden>
              </Box>
            )}
            <Stack gap={2} style={{ minWidth: 0 }}>
              <Text size="sm" fw={unread ? 700 : 400} lineClamp={2}>
                {notification.title}
              </Text>
              {notification.body && (
                <Text size="xs" c="dimmed" lineClamp={2}>
                  {notification.body}
                </Text>
              )}
              <Text size="xs" c="dimmed" title={formatTimestamp(notification.created_at)}>
                {formatRelativeTime(notification.created_at)}
              </Text>
            </Stack>
          </Group>
        </Anchor>
        {unread && (
          <ActionIcon
            variant="subtle"
            aria-label={`Mark read: ${notification.title}`}
            onClick={() => markExplicitlyRead(notification.id)}
            disabled={markReadInFlight}
            style={{ minWidth: 44, minHeight: 44 }}
          >
            <svg
              width={16}
              height={16}
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth={2}
              aria-hidden
            >
              <path d="M5 12l5 5L20 7" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </ActionIcon>
        )}
      </Group>
      {markReadFailure && (
        <MutationErrorState
          title="Couldn't mark this notification read"
          error={markReadFailure.error}
          onRetry={
            isRetryableMutationError(markReadFailure.error)
              ? () => markExplicitlyRead(markReadFailure.notificationId)
              : undefined
          }
          retrying={markReadInFlight}
          onDismiss={dismissMarkReadFailure}
          retryLabel="Try marking this notification read again"
        />
      )}
    </Stack>
  );
}
