// apps/web/src/features/notifications/NotificationItem.tsx
import { ActionIcon, Anchor, Box, Group, Stack, Text, VisuallyHidden } from "@mantine/core";
import { Link } from "react-router-dom";
import { formatRelativeTime, formatTimestamp } from "../../lib/time";
import type { Notification } from "../../lib/types";
import { toRoutePath } from "./deepLink";
import { useMarkRead } from "./mutations";

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
  const unread = notification.read_at === null;

  function open() {
    if (unread) markRead.mutate(notification.id);
    onNavigate?.();
  }

  return (
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
          size="sm"
          aria-label={`Mark read: ${notification.title}`}
          onClick={() => markRead.mutate(notification.id)}
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
  );
}
